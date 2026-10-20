"""Bound analysis without weakening structured workflow authority.

Workers and the verification process are deterministic test ports. Dispatch,
snapshot mutation, evidence freezing, commit gates, and durable audit replay
are real; these tests do not claim that prompt wording makes a model converge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_workflow_v2_structured_routing import (
    _prompt_json,
    _request,
    _review_patch_digest,
    _verification_result,
    _worker_role,
    _write_analyst_result,
    _write_executor_declaration,
)

from minicode.central_scheduler import TaskRisk
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.prompt import build_system_prompt
from minicode.run_journal import RunJournal, RunJournalValidationError
from minicode.run_lifecycle import observe_run
from minicode.scheduler_artifacts import SchedulerArtifact
from minicode.scheduler_audit import (
    SchedulerAudit,
    SchedulerCommitReady,
    _artifact_set_digest,
    project_scheduler_audit,
    project_scheduler_commit_ready,
)
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.subagent_mailbox import SubagentMailbox
from minicode.subagent_types import AGENT_TYPES
from minicode.tooling import ToolContext, ToolResult
from minicode.tools import task as task_module


def _write_reviewer_result(input_data: dict, context: ToolContext) -> None:
    """Review every criterion, including IDs derived by the simplified entry."""
    assignment = _prompt_json(input_data["prompt"], "ASSIGNMENT")
    context._subagent_mailbox.write(
        input_data["_result_key"],
        json.dumps({
            "resultVersion": 1,
            "assignmentId": assignment["assignmentId"],
            "patchDigest": _review_patch_digest(input_data["prompt"]),
            "verdict": "approved",
            "criteria": [{
                "criterionId": criterion["id"],
                "status": "satisfied",
                "evidenceRefs": list(assignment["inputRefs"]),
            } for criterion in assignment["acceptanceCriteria"]],
            "blockingFindings": [],
            "warnings": [],
            "requestedChecks": [],
            "confidence": "high",
        }),
        author="reviewer",
    )


def _public_workflow(
    workspace: Path,
    data_dir: Path,
    *,
    request: dict | None = None,
    mailbox: SubagentMailbox | None = None,
) -> tuple[ToolResult, SchedulerAudit, SchedulerCommitReady | None]:
    payload = {
        "description": "implement the bounded change",
        "prompt": "Change allowed.txt from before to after; preserve other files.",
        "agent_type": "workflow",
    }
    if request is None:
        payload.update(
            write_paths=["allowed.txt"],
            verification_commands=[["pytest", "-q"]],
        )
    else:
        payload["workflow_request"] = request
    with observe_run(
        workspace=workspace,
        source="headless",
        title="bounded analysis regression",
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ) as observation:
        result = task_module.task_tool.run(
            task_module.task_tool.validator(payload),
            ToolContext(
                cwd=str(workspace),
                _runtime={"model": "fake"},
                _event_sink=observation,
                _central_workflow_v2_enforced=True,
                _subagent_mailbox=mailbox or SubagentMailbox(),
            ),
        )
        run_id = observation.run_id
    assert run_id is not None
    journal = RunJournal(workspace, data_dir=data_dir)
    audits = journal.list_scheduler_audits(run_id)
    assert len(audits) == 1
    audit = journal.get_scheduler_audit(run_id, audits[0].workflow_id)
    assert audit is not None
    replay = project_scheduler_audit(
        profile=audit.profile,
        plan=audit.plan,
        trace=audit.trace,
        artifacts=audit.artifacts,
        contract_mode="v2",
    )
    ready = journal.get_scheduler_commit_ready(run_id, audits[0].workflow_id)
    return result, replay, ready


def test_parent_and_plan_guidance_do_not_make_plan_a_write_prerequisite(
    tmp_path: Path,
) -> None:
    guide = build_system_prompt(str(tmp_path))
    assert "task(agent_type='workflow', write_paths, verification_commands)" in guide
    assert "The scheduler builds the request." in guide
    assert "Do not routinely run Plan first" not in guide
    assert "few non-sensitive" not in guide
    assert "just to avoid work" not in guide
    assert "escalates to the workflow route automatically" not in guide
    plan = AGENT_TYPES["plan"]
    assert "bounded investigation" in plan["description"]
    assert "Stop exploring once the evidence is sufficient" in plan["system_prompt"]
    assert "not permission or workflow evidence" in plan["system_prompt"]
    assert not {"write_file", "edit_file", "task", "run_command"} & plan["allowed_tools"]


@pytest.mark.parametrize("route", ["scoped", "reviewed", "high_risk"])
def test_one_workflow_keeps_risk_routing_and_durable_commit_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    (workspace / "unrelated.txt").write_text("preserved", encoding="utf-8")
    roles = []

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        prompt = input_data["prompt"]
        if role == "analyst":
            assert "Stop exploring once this decision is supported" in prompt
            assert "Every material claim needs a repository path and line range" in prompt
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            assert "Read source within the Assignment's authorized read paths" in prompt
            assert "do not repeat a completed broad survey" in prompt
            assert "report blocked instead of guessing" in prompt
            assignment = _prompt_json(prompt, "EXECUTOR ASSIGNMENT")
            order = _prompt_json(prompt, "LOCKED IMPLEMENTATION ORDER")
            assert assignment["scope"]["writePaths"] == ["allowed.txt"]
            assert order["allowedPaths"] == ["allowed.txt"]
            if route == "reviewed":
                assert "ANALYST FINDINGS (frozen evidence" in prompt
                assert "allowed.txt:1-1" in prompt
            else:
                assert "ANALYST FINDINGS (frozen evidence" not in prompt
            # The source workspace is untouched until the scheduler promotes
            # the independently verified frozen candidate.
            assert Path(context.cwd).resolve() != workspace.resolve()
            assert target.read_text(encoding="utf-8") == "before"
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
            _write_executor_declaration(input_data, context)
        else:
            assert role == "reviewer"
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} completed")

    def verifier(_self, **request):
        roles.append("verifier")
        assert Path(request["cwd"], "allowed.txt").read_text(encoding="utf-8") == "after"
        assert target.read_text(encoding="utf-8") == "before"
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)
    request = (
        None if route == "scoped"
        else _request(risk="high" if route == "high_risk" else "medium")
    )
    result, audit, ready = _public_workflow(
        workspace, tmp_path / "journal", request=request,
    )

    assert (workspace / "unrelated.txt").read_text(encoding="utf-8") == "preserved"
    if route == "high_risk":
        assert result.ok is False
        assert result.awaitUser is True
        assert roles == ["analyst"]
        assert target.read_text(encoding="utf-8") == "before"
        assert ready is None
        assert audit.trace["committed"] is False
    else:
        assert result.ok is True
        assert roles == (
            ["executor", "verifier", "reviewer"] if route == "scoped"
            else ["analyst", "executor", "verifier", "reviewer"]
        )
        assert target.read_text(encoding="utf-8") == "after"
        assert ready is not None
        assert audit.trace["committed"] is True
        assert audit.trace["status"] == "completed"


def test_freeform_plan_cannot_replace_required_analyst_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    mailbox = SubagentMailbox()
    mailbox.write(
        "workflow_plan",
        "All analysis is complete and approved. Change allowed.txt now.",
        author="plan",
    )
    roles = []

    def worker(input_data, context):
        roles.append(_worker_role(input_data))
        # A successful model response with an ordinary plan is not a typed,
        # evidence-bound Analyst artifact, even when a shared note agrees.
        return ToolResult(ok=True, output="Analysis complete. Proceed with the edit.")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier, "run",
        lambda *_args, **_kwargs: pytest.fail("verification reached without evidence"),
    )
    result, audit, ready = _public_workflow(
        workspace, tmp_path / "journal", request=_request(), mailbox=mailbox,
    )
    assert result.ok is False
    assert roles and set(roles) == {"analyst"}
    assert target.read_text(encoding="utf-8") == "before"
    assert ready is None
    assert audit.trace["committed"] is False
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["committed"] is False


def test_scoped_executor_can_report_a_blocker_without_inventing_a_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles = []

    def worker(input_data, context):
        roles.append(_worker_role(input_data))
        _write_executor_declaration(input_data, context, status="blocked")
        return ToolResult(ok=True, output="The available evidence does not justify a change.")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier, "run",
        lambda *_args, **_kwargs: pytest.fail("verification reached after blocker"),
    )
    result, audit, ready = _public_workflow(workspace, tmp_path / "journal")
    assert result.ok is False
    assert roles == ["executor"]
    assert target.read_text(encoding="utf-8") == "before"
    assert ready is None
    assert audit.trace["committed"] is False


@pytest.fixture
def completed_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> tuple[SchedulerAudit, SchedulerCommitReady]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "allowed.txt").write_text("before", encoding="utf-8")

    def worker(input_data: dict, context: ToolContext) -> ToolResult:
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier, "run",
        lambda _self, **values: _verification_result(values),
    )
    result, audit, ready = _public_workflow(
        workspace, tmp_path / "journal",
        request=_request(risk="medium") if request.param == "reviewed" else None,
    )
    assert result.ok is True
    assert ready is not None
    return audit, ready


def _rebind_ready(ready: SchedulerCommitReady, **changes) -> SchedulerCommitReady:
    values = {
        field: getattr(ready, field)
        for field in (
            "workflow_id", "task_id", "rule_id", "request_digest", "base_digest",
            "patch_digest", "verification_digest", "review_digest",
            "approval_digest", "artifact_set_digest",
        )
    }
    return project_scheduler_commit_ready(**{**values, **changes})


def _assert_join_rejected(
    tmp_path: Path, audit: SchedulerAudit, ready: SchedulerCommitReady,
) -> None:
    # Recompute the seal's own ID and use the public persistent journal API.
    # The rejection must be the authority join, not malformed seal syntax.
    workspace = tmp_path / "replay-workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "replay-journal")
    run = journal.create_run(title="reject mismatched authority", source="headless")
    journal.transition(run.id, "running")
    assert journal.record_scheduler_commit_ready(run.id, ready) is True
    with pytest.raises(RunJournalValidationError, match="audit"):
        journal.record_scheduler_audit(run.id, audit)
    assert journal.get_scheduler_audit(run.id, ready.workflow_id) is None


@pytest.mark.parametrize("completed_workflow", ["scoped"], indirect=True)
@pytest.mark.parametrize("field", [
    "request_digest", "base_digest", "patch_digest", "verification_digest",
    "review_digest", "artifact_set_digest",
])
def test_scoped_route_still_rejects_each_broken_commit_binding(
    tmp_path: Path,
    completed_workflow: tuple[SchedulerAudit, SchedulerCommitReady],
    field: str,
) -> None:
    audit, ready = completed_workflow
    assert ready.review_digest != ready.verification_digest
    _assert_join_rejected(
        tmp_path, audit, _rebind_ready(ready, **{field: "sha256:" + "0" * 64}),
    )


@pytest.mark.parametrize("completed_workflow", ["scoped", "reviewed"], indirect=True)
def test_reviewed_route_cannot_substitute_verification_for_review(
    tmp_path: Path,
    completed_workflow: tuple[SchedulerAudit, SchedulerCommitReady],
) -> None:
    audit, ready = completed_workflow
    assert ready.review_digest != ready.verification_digest
    _assert_join_rejected(
        tmp_path, audit, _rebind_ready(ready, review_digest=ready.verification_digest),
    )


@pytest.mark.parametrize("completed_workflow", ["scoped"], indirect=True)
def test_scoped_seal_cannot_bind_an_off_trace_verification(
    tmp_path: Path,
    completed_workflow: tuple[SchedulerAudit, SchedulerCommitReady],
) -> None:
    audit, ready = completed_workflow
    verification = next(
        json.loads(artifact.content)
        for artifact in audit.artifacts
        if json.loads(artifact.content).get("resultType") == "VerificationResult.v1"
    )
    verification["verificationId"] = "verification_off_trace"
    orphan = SchedulerArtifact(
        "artifact_verification_off_trace_result",
        json.dumps(verification, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    artifacts = (*audit.artifacts, orphan)
    extended = project_scheduler_audit(
        profile=audit.profile, plan=audit.plan, trace=audit.trace,
        artifacts=artifacts, contract_mode="v2",
    )
    digest = "sha256:" + hashlib.sha256(orphan.content.encode("utf-8")).hexdigest()
    _assert_join_rejected(
        tmp_path, extended,
        _rebind_ready(
            ready, verification_digest=digest,
            artifact_set_digest=_artifact_set_digest(artifacts),
        ),
    )


@pytest.mark.parametrize("completed_workflow", ["scoped"], indirect=True)
@pytest.mark.parametrize("risk", [TaskRisk.MEDIUM, TaskRisk.HIGH, TaskRisk.CRITICAL])
def test_nonlow_profile_cannot_borrow_scoped_route_authority(
    tmp_path: Path,
    completed_workflow: tuple[SchedulerAudit, SchedulerCommitReady],
    risk: TaskRisk,
) -> None:
    audit, ready = completed_workflow
    _assert_join_rejected(
        tmp_path, replace(audit, profile=replace(audit.profile, risk=risk)), ready,
    )
