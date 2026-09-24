from __future__ import annotations

from types import SimpleNamespace

from minicode.run_events import (
    SkillUsageTracker,
    project_skill_loaded_event,
    project_skill_routing_event,
)


_CONTENT_DIGEST = "a" * 64
_ROUTING_DIGEST = "b" * 64


def _skill(**overrides):  # noqa: ANN003, ANN202
    values = {
        "name": "audit",
        "qualified_name": "review/audit",
        "source": "project",
        "directory": "review",
        "score": 3.5,
        "content": "# Audit\n",
        "content_digest": _CONTENT_DIGEST,
        "routing_identity_digest": _ROUTING_DIGEST,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_routing_event_projects_content_and_routing_identity_digests() -> None:
    routing = SimpleNamespace(
        intent_type="review",
        action_type="analyze",
        total_skills=1,
        selected_skills=[_skill()],
        selected=[],
        used_fallback=False,
    )

    payload = project_skill_routing_event(routing)

    assert payload["routingVersion"] == 2
    assert payload["selected"][0]["contentDigest"] == _CONTENT_DIGEST
    assert (
        payload["selected"][0]["routingIdentityDigest"] == _ROUTING_DIGEST
    )


def test_loaded_event_and_usage_tracker_preserve_routing_identity() -> None:
    skill = _skill()

    projected = project_skill_loaded_event(skill)
    tracker = SkillUsageTracker()
    tracker.record(skill)
    loaded, truncated = tracker.snapshot()

    assert projected["routingIdentityDigest"] == _ROUTING_DIGEST
    assert loaded[0]["routingIdentityDigest"] == _ROUTING_DIGEST
    assert truncated is False
