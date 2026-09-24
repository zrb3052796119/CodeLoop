from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.skill_router import skill_load_policy_for_routing
from minicode.skills import discover_skills
from minicode.tooling import (
    SkillLoadBinding,
    SkillLoadPolicy,
    ToolContext,
    ToolRegistry,
    intersect_skill_load_policies,
    skill_load_binding_from_value,
)
from minicode.tools.load_skill import create_load_skill_tool
from minicode.types import AgentStep, ChatMessage, ModelAdapter


class _ScriptedModel(ModelAdapter):
    def __init__(self, steps: list[AgentStep]) -> None:
        self._steps = steps
        self.calls = 0

    def next(
        self,
        messages: list[ChatMessage],
        on_stream_chunk=None,
    ) -> AgentStep:
        step = self._steps[self.calls]
        self.calls += 1
        return step


def _write_skill(root, name: str) -> None:
    target = root / ".mini-code" / "skills" / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {name} instructions",
                "domains: [code]",
                "scopes: [readonly]",
                "tools: [read_file]",
                "supported_operations: [code.explain]",
                "---",
                f"# {name}",
                "",
                f"SECRET-{name.upper()}",
            ]
        ),
        encoding="utf-8",
    )


def _routing(
    *,
    selected=(),
    denied=(),
    deny_all: bool = False,
    unresolved=(),
    ambiguous=(),
    used_fallback: bool = False,
    prompt_mode: str = "routed",
    catalog_bindings=(),
    constraint_vetoed=(),
):
    return SimpleNamespace(
        selected=list(selected),
        selected_skills=list(selected),
        used_fallback=used_fallback,
        prompt_mode=prompt_mode,
        catalog_bindings=tuple(catalog_bindings),
        constraint_vetoed_names=tuple(constraint_vetoed),
        authority_resolution=SimpleNamespace(
            denied=tuple(denied),
            deny_all=deny_all,
            unresolved=tuple(unresolved),
            ambiguous=tuple(ambiguous),
        ),
    )


def _binding(catalog, qualified_name: str) -> SkillLoadBinding:
    summary = next(
        skill for skill in catalog if skill.qualified_name == qualified_name
    )
    binding = skill_load_binding_from_value(summary)
    assert binding is not None
    return binding


def test_routed_turn_can_load_only_the_selected_skill(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    catalog = discover_skills(tmp_path)
    tool = create_load_skill_tool(str(tmp_path), catalog)
    alpha = next(skill for skill in catalog if skill.qualified_name == "alpha")
    policy = skill_load_policy_for_routing(
        _routing(
            selected=(alpha,)
        )
    )
    context = ToolContext(cwd=str(tmp_path), _skill_load_policy=policy)

    allowed = tool.run({"name": "alpha"}, context)
    bypass = tool.run({"name": "beta"}, context)

    assert allowed.ok is True
    assert "SECRET-ALPHA" in allowed.output
    assert bypass.ok is False
    assert "routing policy" in bypass.output
    assert "SECRET-BETA" not in bypass.output


def test_denied_skill_is_rejected_before_its_file_is_read(
    tmp_path,
    monkeypatch,
) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    catalog = discover_skills(tmp_path)
    tool = create_load_skill_tool(str(tmp_path), catalog)
    policy = SkillLoadPolicy(
        allowed_bindings=frozenset({_binding(catalog, "alpha")}),
        reason_code="routed-selection",
    )

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("denied Skill content must not be opened")

    monkeypatch.setattr(Path, "read_text", unexpected_read)
    denied = tool.run(
        {"name": "beta"},
        ToolContext(cwd=str(tmp_path), _skill_load_policy=policy),
    )

    assert denied.ok is False
    assert "routing policy" in denied.output


def test_deny_all_and_unresolved_authority_fail_closed(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    tool = create_load_skill_tool(str(tmp_path))

    for routing in (
        _routing(deny_all=True, prompt_mode="suppressed"),
        _routing(unresolved=("missing-skill",)),
        _routing(ambiguous=("duplicate-name",)),
    ):
        policy = skill_load_policy_for_routing(routing)
        result = tool.run(
            {"name": "alpha"},
            ToolContext(cwd=str(tmp_path), _skill_load_policy=policy),
        )

        assert result.ok is False
        assert "SECRET-ALPHA" not in result.output


def test_inventory_fallback_keeps_explicit_skill_denials(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    catalog = discover_skills(tmp_path)
    tool = create_load_skill_tool(str(tmp_path), catalog)
    policy = skill_load_policy_for_routing(
        _routing(
            denied=("beta",),
            used_fallback=True,
            prompt_mode="inventory_names",
            catalog_bindings=catalog,
        )
    )
    context = ToolContext(cwd=str(tmp_path), _skill_load_policy=policy)

    assert tool.run({"name": "alpha"}, context).ok is True
    denied = tool.run({"name": "beta"}, context)
    assert denied.ok is False
    assert "SECRET-BETA" not in denied.output


def test_inventory_fallback_excludes_workflow_constraint_veto(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    catalog = discover_skills(tmp_path)
    tool = create_load_skill_tool(str(tmp_path), catalog)
    policy = skill_load_policy_for_routing(
        _routing(
            constraint_vetoed=("beta",),
            used_fallback=True,
            prompt_mode="inventory_names",
            catalog_bindings=catalog,
        )
    )
    context = ToolContext(cwd=str(tmp_path), _skill_load_policy=policy)

    assert policy is not None
    assert policy.denied_qualified_names == frozenset({"beta"})
    assert tool.run({"name": "alpha"}, context).ok is True
    denied = tool.run({"name": "beta"}, context)
    assert denied.ok is False
    assert "SECRET-BETA" not in denied.output


def test_explicit_constraint_conflict_closes_skill_loading(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    catalog = discover_skills(tmp_path)
    policy = skill_load_policy_for_routing(
        _routing(
            constraint_vetoed=("alpha",),
            prompt_mode="constraint_conflict",
            catalog_bindings=catalog,
        )
    )

    assert policy is not None
    assert policy.allowed_bindings == frozenset()
    assert policy.denied_qualified_names == frozenset({"alpha"})
    assert policy.reason_code == "explicit-skill-operation-conflict"


def test_direct_skill_binding_does_not_authorize_same_named_nested_skill(
    tmp_path,
) -> None:
    _write_skill(tmp_path, "foo")
    bundle = tmp_path / ".mini-code" / "skills" / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "SKILL_DIR.md").write_text(
        "---\nname: bundle\ndescription: bundled skills\n---\n",
        encoding="utf-8",
    )
    nested = bundle / "foo" / "SKILL.md"
    nested.parent.mkdir()
    nested.write_text(
        "---\nname: foo\ndescription: nested foo\n---\nNESTED-SECRET",
        encoding="utf-8",
    )
    catalog = discover_skills(tmp_path)
    tool = create_load_skill_tool(str(tmp_path), catalog)
    policy = SkillLoadPolicy(
        allowed_bindings=frozenset({_binding(catalog, "foo")}),
        reason_code="routed-selection",
    )
    context = ToolContext(cwd=str(tmp_path), _skill_load_policy=policy)

    bare_result = tool.run({"name": "foo"}, context)
    nested_result = tool.run({"name": "bundle/foo"}, context)

    assert bare_result.ok is False
    assert nested_result.ok is False
    assert "NESTED-SECRET" not in nested_result.output


def test_unique_nested_skill_allows_bare_and_qualified_binding(tmp_path) -> None:
    bundle = tmp_path / ".mini-code" / "skills" / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "SKILL_DIR.md").write_text(
        "---\nname: bundle\ndescription: bundled skills\n---\n",
        encoding="utf-8",
    )
    nested = bundle / "foo" / "SKILL.md"
    nested.parent.mkdir()
    nested.write_text(
        "---\nname: foo\ndescription: nested foo\n---\nNESTED-SECRET",
        encoding="utf-8",
    )
    catalog = discover_skills(tmp_path)
    tool = create_load_skill_tool(str(tmp_path), catalog)
    policy = SkillLoadPolicy(
        allowed_bindings=frozenset({_binding(catalog, "bundle/foo")}),
        reason_code="routed-selection",
    )
    context = ToolContext(cwd=str(tmp_path), _skill_load_policy=policy)

    bare = tool.run({"name": "foo"}, context)
    qualified = tool.run({"name": "bundle/foo"}, context)

    assert bare.ok is True
    assert qualified.ok is True
    assert "NESTED-SECRET" in bare.output


def test_old_route_binding_cannot_authorize_refreshed_skill_before_read(
    tmp_path,
    monkeypatch,
) -> None:
    _write_skill(tmp_path, "alpha")
    old_catalog = discover_skills(tmp_path)
    policy = SkillLoadPolicy(
        allowed_bindings=frozenset({_binding(old_catalog, "alpha")}),
        reason_code="routed-selection",
    )
    target = tmp_path / ".mini-code" / "skills" / "alpha" / "SKILL.md"
    target.write_text(
        "---\nname: alpha\ndescription: replacement\n---\nNEW-SECRET",
        encoding="utf-8",
    )
    refreshed_tool = create_load_skill_tool(str(tmp_path))

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("replacement body must not be opened")

    monkeypatch.setattr(Path, "read_text", unexpected_read)
    result = refreshed_tool.run(
        {"name": "alpha"},
        ToolContext(cwd=str(tmp_path), _skill_load_policy=policy),
    )

    assert result.ok is False
    assert "routing policy" in result.output
    assert "NEW-SECRET" not in result.output


def test_load_tool_detaches_its_bound_catalog_from_caller_mutation(
    tmp_path,
) -> None:
    _write_skill(tmp_path, "alpha")
    catalog = discover_skills(tmp_path)
    original_binding = _binding(catalog, "alpha")
    tool = create_load_skill_tool(str(tmp_path), catalog)
    catalog[0].qualified_name = "replacement"
    catalog[0].source = "user"
    catalog[0].content_digest = "0" * 64
    catalog[0].routing_identity_digest = "1" * 64

    result = tool.run(
        {"name": "alpha"},
        ToolContext(
            cwd=str(tmp_path),
            _skill_load_policy=SkillLoadPolicy(
                allowed_bindings=frozenset({original_binding}),
                reason_code="routed-selection",
            ),
        ),
    )

    assert result.ok is True
    assert "SECRET-ALPHA" in result.output


def test_inventory_catalog_overflow_fails_closed() -> None:
    bindings = tuple(
        SkillLoadBinding(
            qualified_name=f"skill-{index}",
            source="project",
            content_digest=f"{index:064x}"[-64:],
            routing_identity_digest=f"{index + 1:064x}"[-64:],
        )
        for index in range(129)
    )

    policy = skill_load_policy_for_routing(
        _routing(
            used_fallback=True,
            prompt_mode="inventory_names",
            catalog_bindings=bindings,
        )
    )

    assert policy is not None
    assert policy.allowed_bindings == frozenset()
    assert policy.reason_code == "skill-catalog-policy-overflow"


def test_routing_denial_overflow_fails_closed() -> None:
    policy = skill_load_policy_for_routing(
        _routing(
            denied=tuple(f"denied-{index}" for index in range(129)),
            used_fallback=True,
            prompt_mode="inventory_names",
        )
    )

    assert policy is not None
    assert policy.allowed_bindings == frozenset()
    assert policy.denied_qualified_names == frozenset()
    assert policy.reason_code == "skill-denial-policy-overflow"


def test_agent_loop_propagates_turn_skill_load_policy(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    catalog = discover_skills(tmp_path)
    tools = ToolRegistry([create_load_skill_tool(str(tmp_path), catalog)])
    model = _ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "bypass",
                        "toolName": "load_skill",
                        "input": {"name": "beta"},
                    }
                ],
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )
    policy = SkillLoadPolicy(
        allowed_bindings=frozenset({_binding(catalog, "alpha")}),
        reason_code="routed-selection",
    )

    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "sys"}],
        cwd=str(tmp_path),
        skill_load_policy=policy,
    )

    denied_results = [
        message
        for message in messages
        if message.get("role") == "tool_result"
        and message.get("toolName") == "load_skill"
    ]
    assert len(denied_results) == 1
    assert denied_results[0]["isError"] is True
    assert "routing policy" in denied_results[0]["content"]
    assert "SECRET-BETA" not in denied_results[0]["content"]


def test_model_catalog_turn_can_load_an_unselected_catalog_skill(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    catalog = discover_skills(tmp_path)
    tools = ToolRegistry([create_load_skill_tool(str(tmp_path), catalog)])
    model = _ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "model-choice",
                        "toolName": "load_skill",
                        "input": {"name": "beta"},
                    }
                ],
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )
    policy = skill_load_policy_for_routing(
        _routing(prompt_mode="model", catalog_bindings=catalog)
    )
    assert policy is not None
    assert policy.reason_code == "model-catalog"
    # beta was never router-selected; the model chose it from the catalog.
    assert policy.permits(_binding(catalog, "beta"))

    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "sys"}],
        cwd=str(tmp_path),
        skill_load_policy=policy,
    )

    loaded = [
        message
        for message in messages
        if message.get("role") == "tool_result"
        and message.get("toolName") == "load_skill"
    ]
    assert len(loaded) == 1
    assert loaded[0]["isError"] is False
    assert "SECRET-BETA" in loaded[0]["content"]


def test_model_catalog_keeps_explicit_skill_denials(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    catalog = discover_skills(tmp_path)
    tool = create_load_skill_tool(str(tmp_path), catalog)
    policy = skill_load_policy_for_routing(
        _routing(
            denied=("beta",),
            prompt_mode="model",
            catalog_bindings=catalog,
        )
    )
    context = ToolContext(cwd=str(tmp_path), _skill_load_policy=policy)

    assert policy is not None
    assert policy.reason_code == "model-catalog"
    assert policy.denied_qualified_names == frozenset({"beta"})
    assert tool.run({"name": "alpha"}, context).ok is True
    denied = tool.run({"name": "beta"}, context)
    assert denied.ok is False
    assert "SECRET-BETA" not in denied.output


def test_child_policy_still_narrows_a_model_catalog_parent(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    catalog = discover_skills(tmp_path)
    parent = skill_load_policy_for_routing(
        _routing(prompt_mode="model", catalog_bindings=catalog)
    )
    child = SkillLoadPolicy(
        allowed_bindings=frozenset({_binding(catalog, "alpha")}),
        reason_code="routed-selection",
    )

    combined = intersect_skill_load_policies(parent, child)

    assert combined is not None
    assert combined.permits(_binding(catalog, "alpha"))
    assert not combined.permits(_binding(catalog, "beta"))


def test_model_catalog_closes_only_on_deny_all_or_suppression(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    catalog = discover_skills(tmp_path)
    tool = create_load_skill_tool(str(tmp_path), catalog)

    for routing in (
        _routing(deny_all=True, prompt_mode="model", catalog_bindings=catalog),
        _routing(
            deny_all=True,
            prompt_mode="suppressed",
            catalog_bindings=catalog,
        ),
    ):
        policy = skill_load_policy_for_routing(routing)
        assert policy is not None
        assert policy.allowed_bindings == frozenset()
        assert policy.allow_catalog_scope is False
        assert not policy.permits(_binding(catalog, "alpha"))
        assert policy.reason_code == "user-denied-all-skills"
        result = tool.run(
            {"name": "alpha"},
            ToolContext(cwd=str(tmp_path), _skill_load_policy=policy),
        )
        assert result.ok is False
        assert "SECRET-ALPHA" not in result.output


def test_model_catalog_treats_unresolved_and_ambiguous_as_advisory(
    tmp_path,
) -> None:
    _write_skill(tmp_path, "alpha")
    catalog = discover_skills(tmp_path)

    for routing in (
        _routing(
            prompt_mode="model",
            unresolved=("ghost-name",),
            catalog_bindings=catalog,
        ),
        _routing(
            prompt_mode="model",
            ambiguous=("duplicate-name",),
            catalog_bindings=catalog,
        ),
    ):
        policy = skill_load_policy_for_routing(routing)
        assert policy is not None
        assert policy.reason_code == "model-catalog"
        assert policy.permits(_binding(catalog, "alpha"))


def test_model_catalog_over_limit_catalog_stays_catalog_scoped() -> None:
    bindings = tuple(
        SkillLoadBinding(
            qualified_name=f"skill-{index}",
            source="project",
            content_digest=f"{index:064x}"[-64:],
            routing_identity_digest=f"{index + 1:064x}"[-64:],
        )
        for index in range(129)
    )

    policy = skill_load_policy_for_routing(
        _routing(prompt_mode="model", catalog_bindings=bindings)
    )

    assert policy is not None
    assert policy.reason_code == "model-catalog"
    assert policy.allow_catalog_scope is True
    assert policy.allowed_bindings == frozenset()
    assert all(policy.permits(binding) for binding in bindings)


def test_catalog_scoped_policy_permits_matrix() -> None:
    catalog_binding = SkillLoadBinding(
        qualified_name="alpha",
        source="project",
        content_digest="a" * 64,
        routing_identity_digest="b" * 64,
    )
    denied_binding = SkillLoadBinding(
        qualified_name="beta",
        source="project",
        content_digest="c" * 64,
        routing_identity_digest="d" * 64,
    )
    policy = SkillLoadPolicy(
        allow_catalog_scope=True,
        denied_qualified_names=frozenset({"beta"}),
        reason_code="model-catalog",
    )

    assert policy.permits(catalog_binding) is True
    assert policy.permits(denied_binding) is False
    assert policy.permits("alpha") is False
    assert policy.permits(None) is False
    with pytest.raises(ValueError, match="catalog-scoped"):
        SkillLoadPolicy(
            allowed_bindings=frozenset({catalog_binding}),
            allow_catalog_scope=True,
            reason_code="model-catalog",
        )


def test_model_catalog_oversized_turn_loads_a_never_selected_skill(
    tmp_path,
) -> None:
    for index in range(130):
        _write_skill(tmp_path, f"skill-{index:03d}")
    catalog = discover_skills(tmp_path)
    assert len(catalog) == 130
    tools = ToolRegistry([create_load_skill_tool(str(tmp_path), catalog)])
    model = _ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "model-choice",
                        "toolName": "load_skill",
                        "input": {"name": "skill-129"},
                    }
                ],
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )
    policy = skill_load_policy_for_routing(
        _routing(prompt_mode="model", catalog_bindings=catalog)
    )
    assert policy is not None
    assert policy.reason_code == "model-catalog"
    assert policy.allow_catalog_scope is True
    # skill-129 was never router-selected; the model chose it from the catalog.
    assert policy.permits(_binding(catalog, "skill-129"))

    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "sys"}],
        cwd=str(tmp_path),
        skill_load_policy=policy,
    )

    loaded = [
        message
        for message in messages
        if message.get("role") == "tool_result"
        and message.get("toolName") == "load_skill"
    ]
    assert len(loaded) == 1
    assert loaded[0]["isError"] is False
    assert "SECRET-SKILL-129" in loaded[0]["content"]
