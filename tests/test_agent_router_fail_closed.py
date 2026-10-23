from __future__ import annotations

from unittest.mock import Mock

import pytest

from minicode.agent_router import AgentRouter, ModelTier, TaskComplexity
from minicode.model_registry import BUILTIN_MODELS, ModelInfo, Provider
from minicode.model_switcher import ModelSwitcher
from minicode.smart_router import SmartRouter


def catalog(*names: str) -> dict[str, ModelInfo]:
    return {name: ModelInfo(name, Provider.CUSTOM) for name in names}


def tier() -> ModelTier:
    return ModelTier(
        name="project",
        complexity=TaskComplexity.MODERATE,
        primary_model="project-primary",
        fallback_models=["project-fallback-1", "project-fallback-2"],
    )


def test_explicit_empty_catalog_does_not_inherit_builtin_models() -> None:
    router = AgentRouter(available_models={})

    with pytest.raises(RuntimeError):
        router.route_task("list files")

    assert router.get_routing_stats()["total_decisions"] == 0


def test_unspecified_catalog_preserves_default_model_selection() -> None:
    router = AgentRouter(available_models=None)

    decision = router.route_task("list files")

    assert decision.selected_model == "claude-haiku-3-20240307"
    assert decision.selected_model in BUILTIN_MODELS


@pytest.mark.parametrize(
    "available, expected",
    [
        (("unrelated", "project-fallback-2", "project-primary"), "project-primary"),
        (("unrelated", "project-fallback-2", "project-fallback-1"), "project-fallback-1"),
        (("unrelated", "project-fallback-2"), "project-fallback-2"),
    ],
)
def test_declared_candidates_keep_their_priority(
    available: tuple[str, ...], expected: str,
) -> None:
    router = AgentRouter(tiers=[tier()], available_models=catalog(*available))

    decision = router.route_task("review code update")

    assert decision.selected_model == expected
    assert decision.tier_name == "project"


def test_missing_tier_candidates_never_select_an_unrelated_model() -> None:
    router = AgentRouter(tiers=[tier()], available_models=catalog("unrelated"))

    with pytest.raises(RuntimeError):
        router.route_task("review code update")

    assert router.get_routing_stats()["total_decisions"] == 0


@pytest.mark.parametrize("configure_at_construction", [True, False])
def test_registered_explicit_model_overrides_unavailable_tier(
    configure_at_construction: bool,
) -> None:
    router = AgentRouter(
        tiers=[tier()], available_models=catalog("project-custom"),
        force_model="project-custom" if configure_at_construction else None,
    )
    if not configure_at_construction:
        router.force_model_selection("project-custom")

    decision = router.route_task("review code update")

    assert decision.selected_model == "project-custom"
    assert decision.tier_name == "forced"


@pytest.mark.parametrize("configure_at_construction", [True, False])
def test_unregistered_explicit_model_is_rejected(
    configure_at_construction: bool,
) -> None:
    router = AgentRouter(
        tiers=[tier()], available_models=catalog("project-primary"),
        force_model="unregistered" if configure_at_construction else None,
    )
    if not configure_at_construction:
        router.force_model_selection("unregistered")

    with pytest.raises(RuntimeError):
        router.route_task("review code update")

    assert router.get_routing_stats()["total_decisions"] == 0


def test_smart_router_does_not_switch_after_candidate_exhaustion() -> None:
    router = AgentRouter(tiers=[tier()], available_models=catalog("unrelated"))
    switcher = Mock(spec=ModelSwitcher)
    smart = SmartRouter(router=router, switcher=switcher)

    with pytest.raises(RuntimeError):
        smart.route_and_switch("review code update", current_model="current")

    switcher.switch_to.assert_not_called()
    assert router.get_routing_stats()["total_decisions"] == 0
