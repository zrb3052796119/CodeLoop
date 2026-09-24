from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

import minicode.subagent_model_routing as model_routing_module
import minicode.tools.task as task_module
from minicode.subagent_lifecycle import AsyncSubagentLifecycle
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import (
    SkillLoadBinding,
    SkillLoadPolicy,
    ToolContext,
    deserialize_skill_load_policy,
    intersect_skill_load_policies,
    serialize_skill_load_policy,
)


def _binding(
    qualified_name: str,
    *,
    content_digest: str,
    routing_identity_digest: str,
) -> SkillLoadBinding:
    return SkillLoadBinding(
        qualified_name=qualified_name,
        source="project",
        content_digest=content_digest,
        routing_identity_digest=routing_identity_digest,
    )


ALPHA = _binding(
    "project/alpha",
    content_digest="a" * 64,
    routing_identity_digest="b" * 64,
)
BETA = _binding(
    "project/beta",
    content_digest="c" * 64,
    routing_identity_digest="d" * 64,
)
GAMMA = _binding(
    "project/gamma",
    content_digest="e" * 64,
    routing_identity_digest="f" * 64,
)


def _child_beta_routing() -> SimpleNamespace:
    selected = SimpleNamespace(
        name="beta",
        qualified_name=BETA.qualified_name,
        source=BETA.source,
        content_digest=BETA.content_digest,
        routing_identity_digest=BETA.routing_identity_digest,
    )
    return SimpleNamespace(
        selected=[selected],
        selected_skills=[selected],
        used_fallback=False,
        prompt_mode="routed",
        authority_resolution=SimpleNamespace(
            denied=(),
            deny_all=False,
            unresolved=(),
            ambiguous=(),
        ),
    )


def _parent_policies() -> tuple[SkillLoadPolicy, ...]:
    return (
        SkillLoadPolicy(
            allowed_bindings=frozenset(),
            reason_code="user-denied-all-skills",
        ),
        SkillLoadPolicy(
            allowed_bindings=frozenset({ALPHA, BETA}),
            denied_qualified_names=frozenset({"project/beta"}),
            reason_code="inventory-fallback",
        ),
        SkillLoadPolicy(
            allowed_bindings=frozenset({ALPHA}),
            reason_code="routed-selection",
        ),
    )


@pytest.mark.parametrize(
    "parent",
    _parent_policies(),
    ids=("deny-all", "explicit-denial", "selected-other"),
)
def test_nested_policy_never_expands_parent_authority(
    parent: SkillLoadPolicy,
) -> None:
    child = SkillLoadPolicy(
        allowed_bindings=frozenset({BETA}),
        reason_code="routed-selection",
    )

    effective = intersect_skill_load_policies(parent, child)

    assert isinstance(effective, SkillLoadPolicy)
    assert effective.permits(BETA) is False


def test_same_binding_survives_parent_child_intersection() -> None:
    parent = SkillLoadPolicy(
        allowed_bindings=frozenset({ALPHA}),
        reason_code="routed-selection",
    )
    child = SkillLoadPolicy(
        allowed_bindings=frozenset({ALPHA}),
        reason_code="routed-selection",
    )

    effective = intersect_skill_load_policies(parent, child)

    assert isinstance(effective, SkillLoadPolicy)
    assert effective.permits(ALPHA) is True


@pytest.mark.parametrize(
    "replacement",
    (
        SkillLoadBinding(
            qualified_name=ALPHA.qualified_name,
            source="user",
            content_digest=ALPHA.content_digest,
            routing_identity_digest=ALPHA.routing_identity_digest,
        ),
        _binding(
            ALPHA.qualified_name,
            content_digest="9" * 64,
            routing_identity_digest=ALPHA.routing_identity_digest,
        ),
        _binding(
            ALPHA.qualified_name,
            content_digest=ALPHA.content_digest,
            routing_identity_digest="8" * 64,
        ),
    ),
    ids=("source", "content", "routing-identity"),
)
def test_same_qualified_name_with_different_binding_does_not_intersect(
    replacement: SkillLoadBinding,
) -> None:
    parent = SkillLoadPolicy(
        allowed_bindings=frozenset({ALPHA}),
        reason_code="routed-selection",
    )
    child = SkillLoadPolicy(
        allowed_bindings=frozenset({replacement}),
        reason_code="routed-selection",
    )

    effective = intersect_skill_load_policies(parent, child)

    assert isinstance(effective, SkillLoadPolicy)
    assert effective.allowed_bindings == frozenset()
    assert effective.permits(replacement) is False


def test_inventory_snapshot_does_not_authorize_new_child_binding() -> None:
    parent = SkillLoadPolicy(
        allowed_bindings=frozenset({ALPHA, BETA}),
        reason_code="inventory-fallback",
    )
    child = SkillLoadPolicy(
        allowed_bindings=frozenset({GAMMA}),
        reason_code="routed-selection",
    )

    effective = intersect_skill_load_policies(parent, child)

    assert isinstance(effective, SkillLoadPolicy)
    assert effective.allowed_bindings == frozenset()
    assert effective.permits(GAMMA) is False


def test_scoped_scope_intersection_stays_catalog_scoped() -> None:
    parent = SkillLoadPolicy(
        allow_catalog_scope=True,
        denied_qualified_names=frozenset({"project/beta"}),
        reason_code="model-catalog",
    )
    child = SkillLoadPolicy(
        allow_catalog_scope=True,
        denied_qualified_names=frozenset({"project/gamma"}),
        reason_code="model-catalog",
    )

    effective = intersect_skill_load_policies(parent, child)

    assert isinstance(effective, SkillLoadPolicy)
    assert effective.allow_catalog_scope is True
    assert effective.allowed_bindings == frozenset()
    assert effective.permits(ALPHA) is True
    assert effective.permits(BETA) is False
    assert effective.permits(GAMMA) is False


def test_scoped_enumerated_intersection_keeps_the_enumerated_set() -> None:
    scoped = SkillLoadPolicy(
        allow_catalog_scope=True,
        denied_qualified_names=frozenset({"project/beta"}),
        reason_code="model-catalog",
    )
    enumerated = SkillLoadPolicy(
        allowed_bindings=frozenset({ALPHA, BETA}),
        denied_qualified_names=frozenset({"project/gamma"}),
        reason_code="inventory-fallback",
    )

    for parent, child in ((scoped, enumerated), (enumerated, scoped)):
        effective = intersect_skill_load_policies(parent, child)

        assert isinstance(effective, SkillLoadPolicy)
        assert effective.allow_catalog_scope is False
        assert effective.allowed_bindings == frozenset({ALPHA, BETA})
        assert effective.permits(ALPHA) is True
        assert effective.permits(BETA) is False
        assert effective.permits(GAMMA) is False


def test_scoped_policy_payload_round_trip_and_legacy_v2_compatibility() -> None:
    scoped = SkillLoadPolicy(
        allow_catalog_scope=True,
        denied_qualified_names=frozenset({"project/beta"}),
        reason_code="model-catalog",
    )
    payload = serialize_skill_load_policy(scoped)

    assert isinstance(payload, dict)
    assert payload["version"] == 3
    assert payload["allow_catalog_scope"] is True
    restored = deserialize_skill_load_policy(payload)
    assert restored == scoped
    assert restored is not None
    assert restored.permits(ALPHA) is True
    assert restored.permits(BETA) is False

    legacy_payload = {
        "version": 2,
        "allowed_bindings": [
            {
                "qualified_name": ALPHA.qualified_name,
                "source": ALPHA.source,
                "content_digest": ALPHA.content_digest,
                "routing_identity_digest": ALPHA.routing_identity_digest,
            }
        ],
        "denied_qualified_names": [],
        "reason_code": "routed-selection",
    }
    legacy = deserialize_skill_load_policy(legacy_payload)
    assert legacy is not None
    assert legacy.allow_catalog_scope is False
    assert legacy.permits(ALPHA) is True
    assert legacy.permits(BETA) is False

    with pytest.raises(ValueError, match="version"):
        deserialize_skill_load_policy({**payload, "version": 2})
    with pytest.raises(ValueError, match="version"):
        deserialize_skill_load_policy({**legacy_payload, "version": 3})


def test_missing_child_routing_denies_even_inventory_parent() -> None:
    parent = SkillLoadPolicy(
        allowed_bindings=frozenset({ALPHA, BETA}),
        reason_code="inventory-fallback",
    )

    effective = intersect_skill_load_policies(parent, None)

    assert isinstance(effective, SkillLoadPolicy)
    assert effective.allowed_bindings == frozenset()
    assert effective.reason_code == "child-routing-unavailable"


def test_nested_denial_union_overflow_fails_closed() -> None:
    parent = SkillLoadPolicy(
        denied_qualified_names=frozenset(
            f"parent-{index}" for index in range(128)
        ),
        reason_code="inventory-fallback",
    )
    child = SkillLoadPolicy(
        denied_qualified_names=frozenset(
            f"child-{index}" for index in range(128)
        ),
        reason_code="inventory-fallback",
    )

    effective = intersect_skill_load_policies(parent, child)

    assert isinstance(effective, SkillLoadPolicy)
    assert effective.allowed_bindings == frozenset()
    assert effective.denied_qualified_names == frozenset()
    assert effective.reason_code == "nested-denial-overflow"


def test_skill_policy_transport_is_exact_and_bounded() -> None:
    policy = SkillLoadPolicy(
        allowed_bindings=frozenset({ALPHA}),
        denied_qualified_names=frozenset({"project/beta"}),
        reason_code="nested-policy-intersection",
    )
    payload = serialize_skill_load_policy(policy)

    assert deserialize_skill_load_policy(payload) == policy
    assert isinstance(payload, dict)
    assert payload["version"] == 3
    assert "path" not in json.dumps(payload, sort_keys=True)
    with pytest.raises(ValueError, match="fields"):
        deserialize_skill_load_policy({**payload, "unexpected": True})
    with pytest.raises(ValueError, match="binding limit"):
        deserialize_skill_load_policy(
            {
                **payload,
                "allowed_bindings": [
                    {
                        "qualified_name": f"skill-{index}",
                        "source": "project",
                        "content_digest": f"{index:064x}"[-64:],
                        "routing_identity_digest": f"{index + 1:064x}"[-64:],
                    }
                    for index in range(129)
                ],
            }
        )
    malformed_binding = dict(payload["allowed_bindings"][0])
    malformed_binding["qualified_name"] = 123
    with pytest.raises(TypeError, match="fields must be strings"):
        deserialize_skill_load_policy(
            {**payload, "allowed_bindings": [malformed_binding]}
        )


def _stub_nested_turn(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[SkillLoadPolicy | None],
) -> None:
    monkeypatch.setattr(
        model_routing_module,
        "create_subagent_model_adapter",
        lambda runtime, _agent_type, *, tools: SimpleNamespace(runtime=runtime),
    )
    monkeypatch.setattr(
        task_module,
        "_build_sub_agent_system_prompt",
        lambda **_kwargs: ("child system", _child_beta_routing()),
    )

    def fake_turn(**kwargs):
        captured.append(kwargs["skill_load_policy"])
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(task_module, "run_agent_turn", fake_turn)


@pytest.mark.parametrize(
    "parent",
    _parent_policies(),
    ids=("deny-all", "explicit-denial", "selected-other"),
)
def test_in_process_child_rerouting_cannot_bypass_parent_policy(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    parent: SkillLoadPolicy,
) -> None:
    captured: list[SkillLoadPolicy | None] = []
    _stub_nested_turn(monkeypatch, captured)

    result = task_module.task_tool.run(
        {
            "description": "inspect",
            "prompt": "Inspect the implementation.",
            "agent_type": "explore",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _skill_load_policy=parent,
        ),
    )

    assert result.ok is True
    assert len(captured) == 1
    effective = captured[0]
    assert isinstance(effective, SkillLoadPolicy)
    assert effective.permits(BETA) is False


def test_async_child_keeps_parent_policy_through_context_copy(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[SkillLoadPolicy | None] = []
    _stub_nested_turn(monkeypatch, captured)
    parent = SkillLoadPolicy(
        allowed_bindings=frozenset({ALPHA}),
        reason_code="routed-selection",
    )
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    try:
        spawned = task_module.task_tool.run(
            task_module.task_tool.validator(
                {
                    "action": "spawn",
                    "description": "inspect asynchronously",
                    "prompt": "Inspect the implementation.",
                    "agent_type": "explore",
                }
            ),
            ToolContext(
                cwd=str(tmp_path),
                _runtime={"model": "fake"},
                _subagent_lifecycle=lifecycle,
                _skill_load_policy=parent,
            ),
        )
        subagent_id = json.loads(spawned.output)["subagentId"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            terminal = lifecycle.poll(subagent_id)
            if terminal["terminal"]:
                break
            time.sleep(0.001)
        else:
            raise AssertionError("async child did not finish")

        assert terminal["status"] == "completed"
        effective = captured[0]
        assert isinstance(effective, SkillLoadPolicy)
        assert (
            effective.permits(BETA) is False
        )
    finally:
        lifecycle.shutdown()
