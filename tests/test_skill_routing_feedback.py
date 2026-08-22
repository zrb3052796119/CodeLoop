from __future__ import annotations

from copy import deepcopy

from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import parse_intent
from minicode.skill_feedback import (
    SkillRoutingFeedback,
    build_skill_routing_feedback,
    clear_skill_feedback_cache,
)
from minicode.skill_router import SkillRouter, build_skill_router
from minicode.run_events import project_skill_routing_event


_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _cohort(*, runs: int, successes: int, positive: bool) -> dict[str, object]:
    return {
        "runs": runs,
        "goalAchievements": successes,
        "goalAchievementRate": successes / runs,
        "goalAchievementInterval": {
            "lower": 0.8389 if positive else 0.0,
            "upper": 1.0 if positive else 0.1611,
        },
        "verification": {
            "observedRuns": runs,
            "passedRuns": runs if positive else 0,
            "failedRuns": 0 if positive else runs,
            "coverageComplete": True,
        },
        "userSignal": {
            "observedRuns": 3,
            "acceptedRuns": 3 if positive else 0,
            "correctedRuns": 0 if positive else 2,
            "rejectedRuns": 0 if positive else 1,
            "coverageComplete": False,
        },
    }


def _snapshot(
    *,
    digest: str = _DIGEST_B,
    status: str = "positive_signal",
) -> dict[str, object]:
    positive = status == "positive_signal"
    treatment = _cohort(
        runs=20,
        successes=20 if positive else 0,
        positive=positive,
    )
    control = _cohort(
        runs=20,
        successes=0 if positive else 20,
        positive=not positive,
    )
    return {
        "ledgerVersion": 1,
        "mode": "shadow",
        "scannedRuns": 40,
        "runsTruncated": False,
        "journalDiagnostics": 0,
        "evaluationsTruncated": False,
        "evaluations": [
            {
                "skill": {
                    "qualifiedName": "project/auth-b",
                    "source": "project",
                    "directory": "project",
                    "contentDigest": digest,
                },
                "profile": {
                    "intentType": "review",
                    "actionType": "analyze",
                },
                "treatment": treatment,
                "control": control,
                "goalAchievementDelta": 1.0 if positive else -1.0,
                "sampleGatePassed": True,
                "shadowStatus": status,
                "promotionEligible": False,
            }
        ],
        "promotionEligible": False,
    }


def _skills() -> list[dict[str, object]]:
    common = {
        "description": "Review authentication code and implementation.",
        "source": "project",
        "directory": "project",
        "keywords": ["review", "authentication", "code"],
    }
    return [
        {
            **common,
            "name": "auth-a",
            "qualified_name": "project/auth-a",
            "path": "/skills/auth-a/SKILL.md",
            "content_digest": _DIGEST_A,
        },
        {
            **common,
            "name": "auth-b",
            "qualified_name": "project/auth-b",
            "path": "/skills/auth-b/SKILL.md",
            "content_digest": _DIGEST_B,
        },
    ]


def test_feedback_requires_strict_complete_evidence_before_authorizing_rank() -> None:
    feedback = SkillRoutingFeedback.from_snapshot(_snapshot())

    decision = feedback.decision(
        qualified_name="project/auth-b",
        source="project",
        directory="project",
        content_digest=_DIGEST_B,
        intent_type="review",
        action_type="analyze",
    )

    assert decision is not None
    assert decision.adjustment == 0.25
    assert decision.status == "positive_signal"
    assert decision.treatment_runs == 20
    assert decision.control_runs == 20

    truncated = deepcopy(_snapshot())
    truncated["runsTruncated"] = True
    assert SkillRoutingFeedback.from_snapshot(truncated).decision_count == 0

    contradicted = deepcopy(_snapshot())
    contradicted["evaluations"][0]["treatment"]["userSignal"][
        "rejectedRuns"
    ] = 1
    assert SkillRoutingFeedback.from_snapshot(contradicted).decision_count == 0

    forged_statistics = deepcopy(_snapshot())
    forged_statistics["evaluations"][0]["treatment"][
        "goalAchievements"
    ] = 10
    forged_statistics["evaluations"][0]["control"][
        "goalAchievements"
    ] = 10
    # Reported interval/delta still claim a perfect effect. Live authority
    # must recompute from bounded counts instead of trusting those fields.
    assert SkillRoutingFeedback.from_snapshot(forged_statistics).decision_count == 0

    impossible_cohorts = deepcopy(_snapshot())
    impossible_cohorts["scannedRuns"] = 20
    assert SkillRoutingFeedback.from_snapshot(impossible_cohorts).decision_count == 0


def test_feedback_is_digest_and_profile_bound() -> None:
    feedback = SkillRoutingFeedback.from_snapshot(_snapshot())

    for changed in (
        {"content_digest": _DIGEST_A},
        {"intent_type": "debug"},
        {"action_type": "update"},
    ):
        query = {
            "qualified_name": "project/auth-b",
            "source": "project",
            "directory": "project",
            "content_digest": _DIGEST_B,
            "intent_type": "review",
            "action_type": "analyze",
            **changed,
        }
        assert feedback.decision(**query) is None


def test_feedback_reorders_only_candidates_with_independent_query_signal() -> None:
    feedback = SkillRoutingFeedback.from_snapshot(_snapshot())
    router = SkillRouter(routing_feedback=feedback)

    routed = router.route(
        _skills(),
        parse_intent("review authentication code"),
        CapabilityRegistry(),
    )

    assert [item.qualified_name for item in routed.selected] == [
        "project/auth-b",
        "project/auth-a",
    ]
    assert routed.selected[0].score - routed.selected[1].score == 0.25
    assert "evidence:positive_signal(+0.250)" in routed.selected[0].reasons
    assert project_skill_routing_event(routed)["selected"][0][
        "evidenceAdjustment"
    ] == 0.25

    abstained = router.route(
        _skills(),
        parse_intent("tell me a joke about penguins"),
        CapabilityRegistry(),
    )
    assert abstained.selected == []
    assert abstained.used_fallback is True

    explicit = router.route(
        _skills(),
        parse_intent("Use $auth-a to review authentication code."),
        CapabilityRegistry(),
    )
    assert explicit.selected[0].qualified_name == "project/auth-a"
    assert explicit.selected[0].explicitly_requested is True
    assert explicit.selected[0].evidence_adjustment == 0.0


def test_negative_feedback_is_bounded_and_cannot_remove_admission() -> None:
    feedback = SkillRoutingFeedback.from_snapshot(
        _snapshot(status="negative_signal")
    )
    router = SkillRouter(routing_feedback=feedback)

    routed = router.route(
        [_skills()[1]],
        parse_intent("review authentication code"),
        CapabilityRegistry(),
    )

    assert [item.qualified_name for item in routed.selected] == ["project/auth-b"]
    assert "evidence:negative_signal(-0.250)" in routed.selected[0].reasons


def test_production_feedback_builder_is_cached_and_fails_closed(
    tmp_path, monkeypatch
) -> None:
    import minicode.skill_feedback as feedback_module

    snapshots = 0

    class FakeLedger:
        def __init__(self, _journal) -> None:
            pass

        def snapshot(self):
            nonlocal snapshots
            snapshots += 1
            return _snapshot()

    monkeypatch.setattr(feedback_module, "SkillEvidenceLedger", FakeLedger)
    monkeypatch.setattr(feedback_module, "RunJournal", lambda _workspace: object())
    clear_skill_feedback_cache()
    first = build_skill_routing_feedback(tmp_path, cache_ttl_seconds=60.0)
    second = build_skill_routing_feedback(tmp_path, cache_ttl_seconds=60.0)

    assert first is second
    assert first.decision_count == 1
    assert snapshots == 1

    class BrokenLedger:
        def __init__(self, _journal) -> None:
            pass

        def snapshot(self):
            raise RuntimeError("password=private-journal-error")

    monkeypatch.setattr(feedback_module, "SkillEvidenceLedger", BrokenLedger)
    clear_skill_feedback_cache()
    assert build_skill_routing_feedback(tmp_path).decision_count == 0


def test_build_skill_router_connects_production_feedback(tmp_path, monkeypatch) -> None:
    import minicode.skill_feedback as feedback_module
    import minicode.skill_router as router_module

    marker = object()
    captured: dict[str, object] = {}

    class CapturingRouter:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        feedback_module,
        "build_skill_routing_feedback",
        lambda _workspace: marker,
    )
    monkeypatch.setattr(router_module, "SkillRouter", CapturingRouter)

    built = build_skill_router(tmp_path)

    assert isinstance(built, CapturingRouter)
    assert captured == {
        "workspace": tmp_path,
        "routing_feedback": marker,
    }
