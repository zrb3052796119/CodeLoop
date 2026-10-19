"""Historical policy decisions replay under their recorded, closed version."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from minicode.central_scheduler import CentralScheduler, DispatchStage
from minicode.multi_agent_contracts import parse_workflow_request
from minicode.scheduler_audit import SchedulerAuditJournal, SchedulerAuditStorageError


def _restore_policy2_run(tmp_path: Path) -> tuple[Path, str]:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/scheduler-policy-v2-reviewed.json").read_text()
    )
    assert fixture["sourcePolicyVersion"] == 2
    run = tmp_path / "historical-run"
    run.mkdir()
    for relative, content in fixture["files"].items():
        target = run / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    manifest_path = next(run.rglob("manifest.json"))
    return run, manifest_path.parent.name


def test_original_policy2_reviewed_artifacts_remain_readable_after_optional_roles(
    tmp_path: Path,
) -> None:
    run, workflow_id = _restore_policy2_run(tmp_path)

    audit = SchedulerAuditJournal(run).get(workflow_id)

    assert audit is not None
    assert audit.plan.policy_version == 2
    assert audit.plan.rule_id == "route.reviewed_change"
    assert audit.profile.complex is True
    assert audit.profile.acceptance_executable is False
    assert audit.trace["committed"] is True
    request_document = next(
        document
        for artifact in audit.artifacts
        if (document := json.loads(artifact.content)).get("resultType") == "WorkflowRequestArtifact.v1"
    )
    current_request = parse_workflow_request(
        request_document["request"], legacy_task_id=audit.task_id,
    )
    current_plan = CentralScheduler().plan(current_request.task_profile)
    assert current_plan.policy_version == 5
    assert current_plan.rule_id == "route.scoped_change"
    assert current_plan.stages == (
        DispatchStage.EXECUTOR, DispatchStage.VERIFIER, DispatchStage.REVIEWER,
    )


@pytest.mark.parametrize("policy_version", [2, 3])
def test_recorded_scoped_plans_keep_the_original_gate_sequence(policy_version: int) -> None:
    from test_workflow_v2_structured_routing import _request

    request = parse_workflow_request(
        _request(risk="low", complex=False), legacy_task_id="task_historical_scoped",
    )
    old_profile = request.task_profile_for_policy(policy_version)
    old_plan = CentralScheduler().plan(old_profile, policy_version=policy_version)
    assert old_plan.policy_version == policy_version
    assert old_plan.stages == (DispatchStage.EXECUTOR, DispatchStage.VERIFIER)
    current_plan = CentralScheduler().plan(request.task_profile)
    assert current_plan.stages == (*old_plan.stages, DispatchStage.REVIEWER)


@pytest.mark.parametrize("forgery", ["profile", "current-version", "unknown-version"])
def test_recorded_version_does_not_accept_reinterpreted_or_unknown_authority(
    tmp_path: Path, forgery: str,
) -> None:
    run, workflow_id = _restore_policy2_run(tmp_path)
    manifest_path = run / "scheduler-workflows" / workflow_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if forgery == "profile":
        manifest["effectiveProfile"]["acceptanceExecutable"] = True
    else:
        version = 5 if forgery == "current-version" else 6
        manifest["dispatchPlan"]["policyVersion"] = version
        manifest["schedulerTrace"]["policyVersion"] = version
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    )

    with pytest.raises(SchedulerAuditStorageError):
        SchedulerAuditJournal(run).get(workflow_id)
