from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from minicode.agent_loop import run_agent_turn
from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import parse_intent
from minicode.run_events import (
    emit_skill_routing_safely,
    project_skill_routing_event,
)
from minicode.run_journal import EventPage, RunJournal
from minicode.run_lifecycle import observe_run
from minicode.skill_router import SkillRouter
from minicode.skill_evidence import SkillEvidenceLedger
from minicode.skills import discover_skills
from minicode.tooling import ToolRegistry
from minicode.tools.load_skill import create_load_skill_tool
from minicode.types import AgentStep, ChatMessage, ModelAdapter


_DIGEST = "a" * 64
_SKILL = {
    "qualifiedName": "project/memory-audit",
    "source": "project",
    "directory": "project",
    "contentDigest": _DIGEST,
}


class _LoadOnceModel(ModelAdapter):
    def __init__(self) -> None:
        self._calls = 0

    def next(
        self,
        messages: list[ChatMessage],
        on_stream_chunk=None,
    ) -> AgentStep:
        self._calls += 1
        if self._calls == 1:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "load-memory-audit",
                        "toolName": "load_skill",
                        "input": {"name": "memory-audit"},
                    }
                ],
            )
        return AgentStep(type="assistant", content="done")


class _UnusedModel(ModelAdapter):
    def next(
        self,
        messages: list[ChatMessage],
        on_stream_chunk=None,
    ) -> AgentStep:
        return AgentStep(type="assistant", content="unused")


def _record_experience(
    journal: RunJournal,
    *,
    loaded: bool,
    success: bool,
    skill: dict[str, str] = _SKILL,
    intent_type: str = "review",
    action_type: str = "analyze",
    additional_loaded_skill: dict[str, str] | None = None,
    routing_version: int = 2,
    load_attempted: bool = False,
    load_after_outcome: bool = False,
    cost_nano_usd: int | None = None,
    duration_ms: int | None = None,
    cost_unavailable: bool = False,
    malformed_cost_event: bool = False,
    verification_outcomes: tuple[str, ...] = (),
    user_signal: str | None = None,
) -> None:
    routed_skills = [
        skill,
        *(
            [additional_loaded_skill]
            if additional_loaded_skill is not None
            else []
        ),
    ]
    record = journal.create_run(title="private task text", source="headless")
    journal.transition(record.id, "running")
    journal.append_event(
        record.id,
        "skill.routed",
        payload={
            "routingVersion": routing_version,
            "intentType": intent_type,
            "actionType": action_type,
            "totalSkills": len(routed_skills),
            "selectedCount": len(routed_skills),
            "selected": [
                {**selected_skill, "score": 4.25 - index}
                for index, selected_skill in enumerate(routed_skills)
            ],
            "selectedTruncated": False,
            "usedFallback": False,
        },
    )
    if load_attempted:
        journal.append_event(
            record.id,
            "tool.started",
            step=1,
            payload={"toolName": "load_skill"},
        )
    if loaded and not load_after_outcome:
        for loaded_skill in routed_skills:
            journal.append_event(
                record.id,
                "skill.loaded",
                step=1,
                payload={"loadVersion": 1, **loaded_skill},
            )
    if cost_nano_usd is not None or duration_ms is not None or cost_unavailable:
        operation_id = "modelop_" + "d" * 32
        journal.append_event(
            record.id,
            "model.started",
            step=1,
            payload={"operationId": operation_id},
        )
        completed_payload: dict[str, object] = {
            "operationId": operation_id,
            "resultType": "assistant",
            "contentPresent": True,
            "toolCallCount": 0,
            "usage": {
                "source": "provider",
                "inputTokens": 1,
                "outputTokens": 1,
                "cacheReadTokens": 0,
                "cacheCreationTokens": 0,
            },
        }
        if duration_ms is not None:
            completed_payload["durationMs"] = duration_ms
        journal.append_event(
            record.id,
            "model.completed",
            step=1,
            payload=completed_payload,
        )
        if cost_nano_usd is not None:
            journal.append_event(
                record.id,
                "model.costed",
                step=1,
                payload={
                    "costVersion": 1,
                    "operationId": operation_id,
                    "status": "priced",
                    "quality": "provider_usage_catalog_rate",
                    "currency": "USD",
                    "catalogId": "minicode-pricing-test-v1",
                    "catalogModelKey": "test/model",
                    "amountNanoUsd": cost_nano_usd,
                    "components": {
                        "inputNanoUsd": cost_nano_usd,
                        "outputNanoUsd": 0,
                        "cacheReadNanoUsd": 0,
                        "cacheCreationNanoUsd": 0,
                    },
                },
            )
        elif cost_unavailable:
            journal.append_event(
                record.id,
                "model.costed",
                step=1,
                payload={
                    "costVersion": 1,
                    "operationId": operation_id,
                    "status": "unavailable",
                    "quality": "unavailable",
                    "currency": "USD",
                    "catalogId": "minicode-pricing-test-v1",
                    "reason": "model_unpriced",
                },
            )
        if malformed_cost_event:
            journal.append_event(
                record.id,
                "model.costed",
                step=1,
                payload={
                    "costVersion": 1,
                    "operationId": "not-a-model-operation",
                    "status": "unavailable",
                    "quality": "unavailable",
                    "currency": "USD",
                    "catalogId": "minicode-pricing-test-v1",
                    "reason": "model_unpriced",
                },
            )
    for verification_outcome in verification_outcomes:
        journal.append_event(
            record.id,
            "task.verified",
            step=2,
            payload={
                "verificationVersion": 1,
                "kind": "tests",
                "outcome": verification_outcome,
                "source": "test_runner",
            },
        )
    outcome = {
        "outcomeVersion": 1,
        "outcomeStatus": "success" if success else "failed",
        "goalAchieved": success,
        "learningSuccess": success,
        "hadToolErrors": False,
        "errorsRecovered": False,
        "toolErrorCount": 0,
    }
    journal.append_event(record.id, "task.outcome", step=2, payload=outcome)
    if loaded and load_after_outcome:
        for loaded_skill in routed_skills:
            journal.append_event(
                record.id,
                "skill.loaded",
                step=2,
                payload={"loadVersion": 1, **loaded_skill},
            )
    if loaded:
        journal.append_event(
            record.id,
            "skill.attributed",
            step=2,
            payload={
                "attributionVersion": 1,
                "attributionKind": "task_correlation",
                "outcomeStatus": outcome["outcomeStatus"],
                "goalAchieved": success,
                "hadToolErrors": False,
                "errorsRecovered": False,
                "toolErrorCount": 0,
                "loadedSkillCount": len(routed_skills),
                "loadedSkills": routed_skills,
                "loadedSkillsTruncated": False,
            },
        )
    journal.transition(record.id, "completed")
    if user_signal is not None:
        journal.record_user_signal(record.id, user_signal)


def test_ledger_compares_same_profile_single_skill_with_no_skill_controls(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    for _index in range(5):
        _record_experience(journal, loaded=True, success=True)
        _record_experience(journal, loaded=False, success=False)

    snapshot = SkillEvidenceLedger(journal).snapshot()

    assert snapshot["ledgerVersion"] == 1
    assert snapshot["mode"] == "shadow"
    assert snapshot["scannedRuns"] == 10
    assert snapshot["eligibleTreatmentRuns"] == 5
    assert snapshot["eligibleControlRuns"] == 5
    assert snapshot["evaluations"] == [
        {
            "skill": _SKILL,
            "profile": {
                "intentType": "review",
                "actionType": "analyze",
            },
            "treatment": {
                "runs": 5,
                "goalAchievements": 5,
                "goalAchievementRate": 1.0,
                "goalAchievementInterval": {
                    "lower": 0.5655,
                    "upper": 1.0,
                    },
                    "toolErrorRuns": 0,
                    "recoveredErrorRuns": 0,
                    "cost": {
                        "observedRuns": 0,
                        "totalNanoUsd": None,
                        "coverageComplete": False,
                    },
                    "latency": {
                        "observedRuns": 0,
                        "totalDurationMs": None,
                        "coverageComplete": False,
                    },
                    "verification": {
                        "observedRuns": 0,
                        "passedRuns": 0,
                        "failedRuns": 0,
                        "coverageComplete": False,
                    },
                    "userSignal": {
                        "observedRuns": 0,
                        "acceptedRuns": 0,
                        "correctedRuns": 0,
                        "rejectedRuns": 0,
                        "coverageComplete": False,
                    },
                },
            "control": {
                "runs": 5,
                "goalAchievements": 0,
                "goalAchievementRate": 0.0,
                "goalAchievementInterval": {
                    "lower": 0.0,
                    "upper": 0.4345,
                    },
                    "toolErrorRuns": 0,
                    "recoveredErrorRuns": 0,
                    "cost": {
                        "observedRuns": 0,
                        "totalNanoUsd": None,
                        "coverageComplete": False,
                    },
                    "latency": {
                        "observedRuns": 0,
                        "totalDurationMs": None,
                        "coverageComplete": False,
                    },
                    "verification": {
                        "observedRuns": 0,
                        "passedRuns": 0,
                        "failedRuns": 0,
                        "coverageComplete": False,
                    },
                    "userSignal": {
                        "observedRuns": 0,
                        "acceptedRuns": 0,
                        "correctedRuns": 0,
                        "rejectedRuns": 0,
                        "coverageComplete": False,
                    },
                },
            "goalAchievementDelta": 1.0,
            "sampleGatePassed": True,
            "shadowStatus": "positive_signal",
            "promotionEligible": False,
        }
    ]
    assert "private task text" not in str(snapshot)


def test_ledger_does_not_compare_across_profile_or_skill_digest(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    other_digest = {**_SKILL, "contentDigest": "b" * 64}
    for _index in range(5):
        _record_experience(journal, loaded=True, success=True)
        _record_experience(
            journal,
            loaded=False,
            success=False,
            intent_type="code",
            action_type="update",
        )
        _record_experience(
            journal,
            loaded=False,
            success=False,
            skill=other_digest,
        )

    snapshot = SkillEvidenceLedger(journal).snapshot()

    evaluation = snapshot["evaluations"][0]
    assert evaluation["skill"] == _SKILL
    assert evaluation["profile"] == {
        "intentType": "review",
        "actionType": "analyze",
    }
    assert evaluation["treatment"]["runs"] == 5
    assert evaluation["control"]["runs"] == 0
    assert evaluation["goalAchievementDelta"] is None
    assert evaluation["sampleGatePassed"] is False
    assert evaluation["shadowStatus"] == "insufficient_evidence"
    assert evaluation["promotionEligible"] is False


def test_ledger_preserves_exact_cost_and_latency_coverage_per_cohort(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    for _index in range(5):
        _record_experience(
            journal,
            loaded=True,
            success=True,
            cost_nano_usd=100,
            duration_ms=200,
        )
        _record_experience(
            journal,
            loaded=False,
            success=False,
            cost_nano_usd=120,
            duration_ms=240,
        )

    evaluation = SkillEvidenceLedger(journal).snapshot()["evaluations"][0]

    assert evaluation["treatment"]["cost"] == {
        "observedRuns": 5,
        "totalNanoUsd": "500",
        "coverageComplete": True,
    }
    assert evaluation["control"]["cost"] == {
        "observedRuns": 5,
        "totalNanoUsd": "600",
        "coverageComplete": True,
    }
    assert evaluation["treatment"]["latency"] == {
        "observedRuns": 5,
        "totalDurationMs": 1_000,
        "coverageComplete": True,
    }
    assert evaluation["control"]["latency"] == {
        "observedRuns": 5,
        "totalDurationMs": 1_200,
        "coverageComplete": True,
    }


def test_ledger_preserves_verification_and_explicit_user_signal_per_cohort(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    for _index in range(5):
        _record_experience(
            journal,
            loaded=True,
            success=True,
            verification_outcomes=("passed",),
            user_signal="accept",
        )
        _record_experience(
            journal,
            loaded=False,
            success=False,
            verification_outcomes=("passed", "failed"),
            user_signal="correct",
        )

    evaluation = SkillEvidenceLedger(journal).snapshot()["evaluations"][0]

    assert evaluation["treatment"]["verification"] == {
        "observedRuns": 5,
        "passedRuns": 5,
        "failedRuns": 0,
        "coverageComplete": True,
    }
    assert evaluation["control"]["verification"] == {
        "observedRuns": 5,
        "passedRuns": 0,
        "failedRuns": 5,
        "coverageComplete": True,
    }
    assert evaluation["treatment"]["userSignal"] == {
        "observedRuns": 5,
        "acceptedRuns": 5,
        "correctedRuns": 0,
        "rejectedRuns": 0,
        "coverageComplete": True,
    }
    assert evaluation["control"]["userSignal"] == {
        "observedRuns": 5,
        "acceptedRuns": 0,
        "correctedRuns": 5,
        "rejectedRuns": 0,
        "coverageComplete": True,
    }


def test_unavailable_cost_does_not_erase_observed_latency(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    _record_experience(
        journal,
        loaded=True,
        success=True,
        duration_ms=200,
        cost_unavailable=True,
    )
    _record_experience(
        journal,
        loaded=False,
        success=False,
        duration_ms=240,
        cost_unavailable=True,
    )

    evaluation = SkillEvidenceLedger(journal).snapshot()["evaluations"][0]

    assert evaluation["treatment"]["cost"]["observedRuns"] == 0
    assert evaluation["control"]["cost"]["observedRuns"] == 0
    assert evaluation["treatment"]["latency"] == {
        "observedRuns": 1,
        "totalDurationMs": 200,
        "coverageComplete": True,
    }
    assert evaluation["control"]["latency"] == {
        "observedRuns": 1,
        "totalDurationMs": 240,
        "coverageComplete": True,
    }


def test_malformed_cost_event_does_not_erase_valid_model_latency(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    _record_experience(
        journal,
        loaded=True,
        success=True,
        cost_nano_usd=100,
        duration_ms=200,
        malformed_cost_event=True,
    )
    _record_experience(
        journal,
        loaded=False,
        success=False,
        cost_nano_usd=120,
        duration_ms=240,
    )

    evaluation = SkillEvidenceLedger(journal).snapshot()["evaluations"][0]

    assert evaluation["treatment"]["cost"] == {
        "observedRuns": 0,
        "totalNanoUsd": None,
        "coverageComplete": False,
    }
    assert evaluation["treatment"]["latency"] == {
        "observedRuns": 1,
        "totalDurationMs": 200,
        "coverageComplete": True,
    }


def test_ledger_excludes_multi_skill_runs_from_effectiveness_comparison(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )

    _record_experience(
        journal,
        loaded=True,
        success=True,
        additional_loaded_skill={
            "qualifiedName": "project/code-review",
            "source": "project",
            "directory": "project",
            "contentDigest": "c" * 64,
        },
    )

    snapshot = SkillEvidenceLedger(journal).snapshot()

    assert snapshot["eligibleTreatmentRuns"] == 0
    assert snapshot["eligibleControlRuns"] == 0
    assert snapshot["excludedRuns"]["ambiguousSkillUse"] == 1
    assert snapshot["evaluations"] == []
    assert snapshot["promotionEligible"] is False


def test_ledger_pages_across_run_journal_without_losing_controls(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    for _index in range(5):
        _record_experience(journal, loaded=True, success=True)
    for _index in range(100):
        _record_experience(journal, loaded=False, success=False)

    snapshot = SkillEvidenceLedger(journal).snapshot()

    assert snapshot["scannedRuns"] == 105
    assert snapshot["runsTruncated"] is False
    assert snapshot["eligibleTreatmentRuns"] == 5
    assert snapshot["eligibleControlRuns"] == 100
    assert snapshot["evaluations"][0]["control"]["runs"] == 100
    assert snapshot["evaluations"][0]["shadowStatus"] == "positive_signal"


def test_production_equivalent_runs_build_the_shadow_cohort(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill_path = (
        workspace
        / ".mini-code"
        / "skills"
        / "memory-audit"
        / "SKILL.md"
    )
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: memory-audit\n"
        "description: Review persistent memory and Skill routing.\n"
        "---\n"
        "# Memory Audit\n",
        encoding="utf-8",
    )
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "journal-home" / ".mini-code",
    )
    routing = SkillRouter().route(
        [asdict(skill) for skill in discover_skills(workspace)],
        parse_intent("review persistent memory and skill routing"),
        CapabilityRegistry(),
    )
    routing_payload = project_skill_routing_event(routing)
    assert routing_payload["routingVersion"] == 2
    assert routing_payload["selectedCount"] == 1
    tools = ToolRegistry([create_load_skill_tool(str(workspace))])

    for loaded in (True, False):
        for index in range(5):
            with observe_run(
                workspace=workspace,
                source="headless",
                title=f"password=private-task-{loaded}-{index}",
                journal_factory=lambda _workspace: journal,
            ) as observation:
                emit_skill_routing_safely(observation, routing)
                run_agent_turn(
                    model=_LoadOnceModel() if loaded else _UnusedModel(),
                    tools=tools,
                    messages=[
                        {"role": "system", "content": "SYSTEM"},
                        {"role": "user", "content": "private task content"},
                    ],
                    cwd=str(workspace),
                    event_sink=observation,
                    max_steps=3 if loaded else 0,
                    enable_work_chain=False,
                )

    snapshot = SkillEvidenceLedger(journal).snapshot()

    assert snapshot["scannedRuns"] == 10
    assert snapshot["eligibleTreatmentRuns"] == 5, snapshot["excludedRuns"]
    assert snapshot["eligibleControlRuns"] == 5
    evaluation = snapshot["evaluations"][0]
    assert evaluation["treatment"]["goalAchievements"] == 5
    assert evaluation["control"]["goalAchievements"] == 0
    assert evaluation["shadowStatus"] == "positive_signal"
    assert evaluation["promotionEligible"] is False
    assert "private-task" not in str(snapshot)


def test_ledger_keeps_legacy_routing_visible_but_out_of_version_evidence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )

    _record_experience(
        journal,
        loaded=False,
        success=True,
        routing_version=1,
    )

    snapshot = SkillEvidenceLedger(journal).snapshot()

    assert snapshot["scannedRuns"] == 1
    assert snapshot["eligibleTreatmentRuns"] == 0
    assert snapshot["eligibleControlRuns"] == 0
    assert snapshot["excludedRuns"]["legacyRouting"] == 1
    assert snapshot["evaluations"] == []


def test_ledger_excludes_failed_skill_load_attempts_from_controls(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )

    _record_experience(
        journal,
        loaded=False,
        success=False,
        load_attempted=True,
    )

    snapshot = SkillEvidenceLedger(journal).snapshot()

    assert snapshot["eligibleControlRuns"] == 0
    assert snapshot["excludedRuns"]["inconsistentSkillUse"] == 1
    assert snapshot["evaluations"] == []


def test_ledger_excludes_out_of_order_skill_use_events(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )

    _record_experience(
        journal,
        loaded=True,
        success=True,
        load_after_outcome=True,
    )

    snapshot = SkillEvidenceLedger(journal).snapshot()

    assert snapshot["eligibleTreatmentRuns"] == 0
    assert snapshot["excludedRuns"]["inconsistentSkillUse"] == 1
    assert snapshot["evaluations"] == []


def test_ledger_excludes_runs_with_incomplete_event_reads(
    tmp_path: Path,
) -> None:
    class DiagnosticJournal:
        def __init__(self, delegate: RunJournal) -> None:
            self._delegate = delegate

        def list_runs(self, **kwargs):
            return self._delegate.list_runs(**kwargs)

        def list_events(self, run_id: str, **kwargs):
            page = self._delegate.list_events(run_id, **kwargs)
            return EventPage(
                items=page.items,
                limit=page.limit,
                has_more=page.has_more,
                next_cursor=page.next_cursor,
                diagnostics=(
                    {
                        "code": "event_read_failed",
                        "message": "password=private-diagnostic",
                    },
                ),
            )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    _record_experience(journal, loaded=False, success=True)

    snapshot = SkillEvidenceLedger(
        DiagnosticJournal(journal),  # type: ignore[arg-type]
    ).snapshot()

    assert snapshot["eligibleControlRuns"] == 0
    assert snapshot["excludedRuns"]["eventReadIncomplete"] == 1
    assert snapshot["journalDiagnostics"] == 1
    assert "private-diagnostic" not in str(snapshot)
