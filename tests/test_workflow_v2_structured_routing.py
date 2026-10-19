"""Public WorkflowRequest routes through the smallest safe production flow."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

import pytest

import minicode.task_deadline as task_deadline_module
import minicode.tools.task as task_module
import minicode.workflow_finalization as workflow_finalization_module
import minicode.workflow_runner as workflow_runner_module
from minicode.central_scheduler import CentralScheduler
from minicode.conflict_port_runner import BoundedConflictPortRunner
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.run_journal import (
    RunJournal,
    RunJournalStorageError,
    RunJournalValidationError,
)
from minicode.run_lifecycle import observe_run
from minicode.scheduler_artifacts import (
    MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW,
    MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES,
    SchedulerArtifact,
    SchedulerArtifactError,
    SchedulerArtifactStore,
)
from minicode.scheduler_audit import (
    SchedulerAuditValidationError,
    project_scheduler_audit,
    project_scheduler_commit_ready,
)
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.subagent_mailbox import SubagentMailbox
from minicode.subagent_result import extract_subagent_result
from minicode.tooling import ToolContext, ToolResult
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
)
from minicode.workflow_workspace import (
    FrozenWorkflowChange,
    WorkflowWorkspaceTransaction,
)


def _request(
    *,
    kind: str = "change",
    # Review tests request medium risk explicitly through this default;
    # complexity alone must not add an Analyst or Reviewer. Scoped-route
    # tests opt in with risk="low".
    risk: str = "medium",
    complex: bool = True,
):
    return {
        "requestVersion": 1,
        "profile": {
            "taskId": "task_structured_route",
            "kind": kind,
            "risk": risk,
            "complex": complex,
            "parentHasSufficientContext": True,
        },
        "scope": {
            "readRoots": ["."],
            "writePaths": ["allowed.txt"] if kind == "change" else [],
            "forbiddenPaths": [".env", ".git"],
        },
        "criteria": [
            {"id": "ac1", "kind": "behavior", "text": "The target is updated."}
        ],
        "verificationChecks": [
            {
                "checkId": "check_tests",
                "kind": "tests",
                "argv": ["pytest", "-q"],
                "criterionIds": ["ac1"],
            }
        ],
    }


def _verification_result(request):
    checks = request.get("checks")
    check = checks[0] if isinstance(checks, tuple) and checks else None
    check_id = str(getattr(check, "check_id", "check_tests"))
    check_kind = str(getattr(check, "kind", "tests"))
    argv = list(getattr(check, "argv", ("pytest", "-q")))
    artifact_ref = (
        "artifact_verification_"
        + hashlib.sha256(
            (
                f"{request['workflow_id']}\0"
                f"{request['execution_assignment_id']}\0"
                f"{request['patch_digest']}\0{check_id}"
            ).encode("utf-8")
        ).hexdigest()[:24]
    )
    empty_stream = {
        "byteCount": 0,
        "digest": "sha256:" + hashlib.sha256(b"").hexdigest(),
        "preview": "",
        "previewBase64": "",
        "truncated": False,
    }
    evidence_sink = request.get("evidence_sink")
    if evidence_sink is not None:
        evidence_sink(
            artifact_ref,
            {
                "evidenceVersion": 1,
                "checkId": check_id,
                "kind": check_kind,
                "argv": argv,
                "resolvedExecutable": None,
                "commandDigest": "sha256:"
                + hashlib.sha256("\0".join(argv).encode("utf-8")).hexdigest(),
                "status": "passed",
                "exitCode": 0,
                "durationMs": 1,
                "failureReason": None,
                "stdout": empty_stream,
                "stderr": dict(empty_stream),
            },
        )
    return {
        "resultType": "VerificationResult.v1",
        "verificationVersion": 1,
        "verificationId": "verification_structured_route",
        "workflowId": request["workflow_id"],
        "taskId": request["task_id"],
        "executionAssignmentId": request["execution_assignment_id"],
        "patchDigest": request["patch_digest"],
        "status": "passed",
        "checks": [
            {
                "checkId": check_id,
                "kind": check_kind,
                "status": "passed",
                "exitCode": 0,
                "durationMs": 1,
                "artifactRef": artifact_ref,
            }
        ],
    }


def _analyst_result_document(
    input_data,
    *,
    prepare_change: bool,
    unknowns: list[str] | None = None,
    risks: list[dict] | None = None,
    step_allowed_paths: list[str] | None = None,
) -> dict:
    assignment_match = re.search(
        r'"assignmentId":\s*"([^"]+)"',
        input_data["prompt"],
    )
    assert assignment_match is not None
    return {
        "resultVersion": 1,
        "assignmentId": assignment_match.group(1),
        "status": "completed",
        "findings": [
            {
                "claimId": "claim_route",
                "statement": "The bounded route is supported.",
                "evidence": [
                    {
                        "path": "allowed.txt" if prepare_change else "README.md",
                        "lineStart": 1,
                        "lineEnd": 1,
                    }
                ],
                "confidence": "high",
            }
        ],
        "unknowns": unknowns or [],
        "risks": risks or [],
        "implementationOrderCandidate": {
            "required": prepare_change,
            "steps": (
                [
                    {
                        "id": "s_route",
                        "action": "Apply the bounded change.",
                        "allowedPaths": (
                            ["allowed.txt"]
                            if step_allowed_paths is None
                            else step_allowed_paths
                        ),
                    }
                ]
                if prepare_change
                else []
            ),
            "verificationRequests": [],
        },
        "ballot": None,
    }


def _write_analyst_result(
    input_data,
    context,
    *,
    prepare_change: bool,
    unknowns: list[str] | None = None,
    risks: list[dict] | None = None,
    step_allowed_paths: list[str] | None = None,
) -> None:
    context._subagent_mailbox.write(
        input_data["_result_key"],
        json.dumps(
            _analyst_result_document(
                input_data,
                prepare_change=prepare_change,
                unknowns=unknowns,
                risks=risks,
                step_allowed_paths=step_allowed_paths,
            )
        ),
        author="analyst",
    )


def _worker_role(input_data: dict) -> str:
    """Classify the scheduler-owned role from its locked result key/prompt."""

    result_key = input_data.get("_result_key")
    prompt = input_data["prompt"]
    if isinstance(result_key, str) and '"role": "adjudicator"' in prompt:
        return "adjudicator"
    if isinstance(result_key, str) and '"role": "analyst"' in prompt:
        return "analyst"
    if isinstance(result_key, str) and '"role": "reviewer"' in prompt:
        return "reviewer"
    if isinstance(result_key, str) and '"role": "executor"' in prompt:
        return "executor"
    raise AssertionError("worker prompt did not carry one known locked role")


def _write_adjudication_result(
    input_data,
    context,
    *,
    decision: str,
) -> None:
    assignment = _prompt_json(input_data["prompt"], "ASSIGNMENT")
    package = _prompt_json(input_data["prompt"], "CONFLICT PACKAGE")
    claims = package["claims"]
    resolved = [
        {
            "claimId": claim["claimId"],
            "resolution": (
                "Selected: the frozen evidence supports this claim."
                if claim["choice"] == "proceed_under_locked_classification"
                and decision == "select_b"
                or claim["choice"] == "stop_for_reclassification"
                and decision == "select_a"
                else "Rejected: the frozen evidence does not support it."
            ),
            "evidenceRefs": claim["evidenceRefs"][:1],
        }
        for claim in claims
    ]
    context._subagent_mailbox.write(
        input_data["_result_key"],
        json.dumps(
            {
                "resultVersion": 1,
                "assignmentId": assignment["assignmentId"],
                "conflictId": package["conflictId"],
                "decision": decision,
                "resolvedClaims": resolved if decision != "insufficient" else [],
                "conditions": [],
                "confidence": "high",
            }
        ),
        author="adjudicator",
    )


def _review_patch_digest(prompt: str) -> str:
    digests = set(re.findall(r'"patchDigest": "(sha256:[0-9a-f]{64})"', prompt))
    assert len(digests) == 1
    return digests.pop()


def _prompt_json(prompt: str, label: str) -> dict:
    match = re.search(rf"{re.escape(label)}:\n([^\n]+)", prompt)
    assert match is not None
    value = json.loads(match.group(1))
    assert isinstance(value, dict)
    return value


def _write_executor_declaration(input_data: dict, context, *, status="completed"):
    assignment = _prompt_json(input_data["prompt"], "EXECUTOR ASSIGNMENT")
    order = _prompt_json(input_data["prompt"], "LOCKED IMPLEMENTATION ORDER")
    result = {
        "resultVersion": 1,
        "assignmentId": assignment["assignmentId"],
        "orderId": order["orderId"],
        "status": status,
        "stepResults": [
            {
                "stepId": step["id"],
                "status": "completed" if status == "completed" else "skipped",
                "artifactRefs": [],
            }
            for step in order["steps"]
        ],
        "deviations": [],
        "selfChecks": [],
        "blockers": [] if status == "completed" else ["executor blocked"],
    }
    context._subagent_mailbox.write(
        input_data["_result_key"],
        json.dumps(result),
        author="executor",
    )


def _write_reviewer_result(
    input_data: dict,
    context,
    *,
    patch_digest: str | None = None,
    evidence_refs: list[str] | None = None,
) -> None:
    result_key = input_data["_result_key"]
    assignment_match = re.search(
        r'"assignmentId":\s*"([^"]+)"',
        input_data["prompt"],
    )
    assert assignment_match is not None
    assignment = _prompt_json(input_data["prompt"], "ASSIGNMENT")
    result = {
        "resultVersion": 1,
        "assignmentId": assignment_match.group(1),
        "patchDigest": patch_digest or _review_patch_digest(input_data["prompt"]),
        "verdict": "approved",
        "criteria": [
            {
                "criterionId": "ac1",
                "status": "satisfied",
                "evidenceRefs": (
                    list(assignment["inputRefs"])
                    if evidence_refs is None
                    else evidence_refs
                ),
            }
        ],
        "blockingFindings": [],
        "warnings": [],
        "requestedChecks": [],
        "confidence": "high",
    }
    context._subagent_mailbox.write(
        result_key,
        json.dumps(result),
        author="reviewer",
    )


def _run(
    tmp_path: Path,
    request: dict,
    monkeypatch: pytest.MonkeyPatch,
    *,
    event_sink=None,
    approval_provider=None,
    cancellation_token=None,
    central_enforced: bool = False,
    commit_audit_waived: bool = False,
    task_deadline_monotonic: float | None = None,
):
    return task_module.task_tool.run(
        task_module.task_tool.validator(
            {
                "description": "structured workflow route",
                "prompt": "Apply the structured request.",
                "agent_type": "workflow",
                "workflow_request": request,
            }
        ),
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _event_sink=event_sink,
            _central_workflow_v2_enforced=central_enforced,
            _workflow_commit_audit_waived=commit_audit_waived,
            _workflow_human_approval_provider=approval_provider,
            _cancellation_token=cancellation_token,
            _task_deadline_monotonic=task_deadline_monotonic,
            _subagent_mailbox=SubagentMailbox(),
        ),
    )


def test_public_low_scoped_change_takes_executor_verifier_reviewer_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    roles: list[str] = []

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after", encoding="utf-8"
            )
            _write_executor_declaration(input_data, context)
        else:
            assert role == "reviewer"
            assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "before"
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} completed")

    def verifier(self, **request):
        roles.append("verifier")
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(
        tmp_path,
        _request(risk="low", complex=False),
        monkeypatch,
        commit_audit_waived=True,
    )

    # A narrow low-risk scope skips Analyst, but passing commands still need
    # semantic review before the frozen candidate can be promoted.
    assert result.ok is True
    assert roles == ["executor", "verifier", "reviewer"]
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "after"
    projected = extract_subagent_result(result.output)
    assert projected is not None
    assert projected["files"] == [{"path": "allowed.txt", "action": "modified"}]
    assert projected["verification"]["status"] == "passed"
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["ruleId"] == "route.scoped_change"
    assert trace["status"] == "completed"
    assert trace["committed"] is True
    assert [
        transition["stage"]
        for transition in trace["transitions"]
    ].count("analyst") == 0
    assert [
        transition["outcome"] for transition in trace["transitions"]
        if transition["stage"] == "reviewer"
    ] == ["dispatched", "completed"]


def test_executor_order_narrowing_write_scope_passes_sealed_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            # The Analyst locks an implementation order for "allowed.txt" only,
            # a strict subset of the Assignment write scope.
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} completed")

    def verifier(self, **request):
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    request = _request()
    request["scope"]["writePaths"] = ["allowed.txt", "second.txt"]

    result = _run(
        tmp_path,
        request,
        monkeypatch,
        event_sink=DurableSink(),
        commit_audit_waived=True,
    )

    assert result.ok is True
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "after"
    # The sealed audit must accept the narrowed order scope on replay: subset
    # of the Assignment write scope is legal, only widening is rejected.
    assert len(audits) == 1
    replayed = project_scheduler_audit(
        profile=audits[0].profile,
        plan=audits[0].plan,
        trace=audits[0].trace,
        artifacts=audits[0].artifacts,
        contract_mode="v2",
    )
    assert replayed.trace == audits[0].trace


def test_nested_structured_workflow_keeps_scope_relative_to_invocation_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "root-secret.txt").write_text("secret", encoding="utf-8")
    nested = tmp_path / "sub"
    nested.mkdir()
    target = nested / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    verifier_observations: list[tuple[bool, bool]] = []

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} completed")

    def verifier(self, **request):
        verification_cwd = Path(request["cwd"])
        verifier_observations.append(
            (
                Path(verification_cwd, "allowed.txt").is_file(),
                Path(verification_cwd.parent, "root-secret.txt").exists(),
            )
        )
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(
        nested,
        _request(),
        monkeypatch,
        commit_audit_waived=True,
    )

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "after"
    assert verifier_observations == [(True, False)]
    projected = extract_subagent_result(result.output)
    assert projected is not None
    assert projected["files"] == [{"path": "allowed.txt", "action": "modified"}]


def test_structured_scope_violation_discards_snapshot_before_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier_called = False

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
            return ToolResult(ok=True, output="analysis complete")
        assert role == "executor"
        Path(context.cwd, "outside.txt").write_text("outside", encoding="utf-8")
        _write_executor_declaration(input_data, context)
        return ToolResult(ok=True, output="implemented outside scope")

    def verifier(self, **request):
        nonlocal verifier_called
        verifier_called = True
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(tmp_path, _request(), monkeypatch)

    assert result.ok is False
    assert verifier_called is False
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("risk", ["high", "critical"])
def test_high_risk_request_stops_after_analysis_at_human_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    risk: str,
) -> None:
    (tmp_path / "allowed.txt").write_text("evidence\n", encoding="utf-8")
    roles = []

    def worker(input_data, context):
        roles.append(input_data["agent_type"])
        _write_analyst_result(input_data, context, prepare_change=True)
        return ToolResult(ok=True, output="risk analysis")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(tmp_path, _request(risk=risk), monkeypatch)

    assert result.ok is False
    assert result.awaitUser is True
    assert roles == ["plan"]
    assert "human_approval_required" in result.output
    assert "approvalRequestId: approval_" in result.output


def test_host_bound_human_approval_resumes_the_full_high_risk_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []
    audits = []

    class ApprovalProvider:
        def consume_approval(self, **request):
            return {
                "approvalVersion": 1,
                "approvalRequestId": request["approval_request_id"],
                "taskId": request["task_id"],
                "requestDigest": request["request_digest"],
                "baseDigest": request["base_digest"],
                "analystDigest": request["analyst_digest"],
                "decision": "approved",
                "approvedBy": "human_test_reviewer",
                "expiresAt": "2999-01-01T00:00:00Z",
            }

    class AuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(
                input_data,
                context,
                prepare_change=True,
                risks=[
                    {
                        "severity": "high",
                        "statement": "The locked high-risk route needs approval.",
                        "evidenceRefs": ["claim_route"],
                    }
                ],
            )
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    def verifier(self, **request):
        roles.append("verifier")
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(
        tmp_path,
        _request(risk="high", complex=True),
        monkeypatch,
        event_sink=AuditSink(),
        approval_provider=ApprovalProvider(),
        commit_audit_waived=True,
    )

    assert result.ok is True
    assert roles == ["analyst", "executor", "verifier", "reviewer"]
    assert target.read_text(encoding="utf-8") == "after"
    assert len(audits) == 1
    assert audits[0].profile.human_confirmation_granted is True
    assert any(
        json.loads(artifact.content).get("resultType") == "HumanApproval.v1"
        for artifact in audits[0].artifacts
    )


@pytest.mark.parametrize(
    ("expire_on_check", "expected_roles"),
    [
        (1, ["analyst"]),
        (2, ["analyst", "executor"]),
        (3, ["analyst", "executor", "verifier"]),
    ],
)
def test_expired_human_approval_is_audited_at_each_privileged_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expire_on_check: int,
    expected_roles: list[str],
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []
    audits = []
    approval_checks = 0

    class ApprovalProvider:
        def consume_approval(self, **request):
            return {
                "approvalVersion": 1,
                "approvalRequestId": request["approval_request_id"],
                "taskId": request["task_id"],
                "requestDigest": request["request_digest"],
                "baseDigest": request["base_digest"],
                "analystDigest": request["analyst_digest"],
                "decision": "approved",
                "approvedBy": "human_expiry_reviewer",
                "expiresAt": "2999-01-01T00:00:00Z",
            }

    class AuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def approval_is_current(_document):
        nonlocal approval_checks
        approval_checks += 1
        return approval_checks < expire_on_check

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            pytest.fail("Reviewer must not run after approval expiry")
        return ToolResult(ok=True, output=f"{role} complete")

    def verifier(self, **request):
        roles.append("verifier")
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        workflow_runner_module,
        "_human_approval_is_current",
        approval_is_current,
    )
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(
        tmp_path,
        _request(risk="high", complex=True),
        monkeypatch,
        event_sink=AuditSink(),
        approval_provider=ApprovalProvider(),
        central_enforced=True,
    )

    assert result.ok is False
    assert result.awaitUser is True
    assert roles == expected_roles
    assert target.read_text(encoding="utf-8") == "before"
    assert len(audits) == 1
    assert audits[0].trace["terminalReason"] == "human_approval_expired"
    assert (
        project_scheduler_audit(
            profile=audits[0].profile,
            plan=audits[0].plan,
            trace=audits[0].trace,
            artifacts=audits[0].artifacts,
            contract_mode="v2",
        ).trace
        == audits[0].trace
    )


def test_human_approval_request_is_stable_across_wait_then_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []

    class ApprovalProvider:
        approved = False
        requests: list[dict[str, object]] = []

        def consume_approval(self, **request):
            self.requests.append(dict(request))
            if not self.approved:
                return None
            first = self.requests[0]
            assert request["approval_request_id"] == first["approval_request_id"]
            assert request["analyst_digest"] == first["analyst_digest"]
            return {
                "approvalVersion": 1,
                "approvalRequestId": request["approval_request_id"],
                "taskId": request["task_id"],
                "requestDigest": request["request_digest"],
                "baseDigest": request["base_digest"],
                "analystDigest": request["analyst_digest"],
                "decision": "approved",
                "approvedBy": "human_resume_reviewer",
                "expiresAt": "2999-01-01T00:00:00Z",
            }

    provider = ApprovalProvider()

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    waiting = _run(
        tmp_path,
        _request(risk="high", complex=True),
        monkeypatch,
        approval_provider=provider,
        commit_audit_waived=True,
    )
    assert waiting.awaitUser is True
    first_request_id = provider.requests[0]["approval_request_id"]
    assert str(first_request_id) in waiting.output
    assert target.read_text(encoding="utf-8") == "before"

    provider.approved = True
    resumed = _run(
        tmp_path,
        _request(risk="high", complex=True),
        monkeypatch,
        approval_provider=provider,
        commit_audit_waived=True,
    )

    assert resumed.ok is True
    assert provider.requests[1]["approval_request_id"] == first_request_id
    assert roles == ["analyst", "analyst", "executor", "reviewer"]
    assert target.read_text(encoding="utf-8") == "after"


def test_critical_read_only_request_can_resume_through_human_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    roles: list[str] = []

    class ApprovalProvider:
        def consume_approval(self, **request):
            return {
                "approvalVersion": 1,
                "approvalRequestId": request["approval_request_id"],
                "taskId": request["task_id"],
                "requestDigest": request["request_digest"],
                "baseDigest": request["base_digest"],
                "analystDigest": request["analyst_digest"],
                "decision": "approved",
                "approvedBy": "human_read_reviewer",
                "expiresAt": "2999-01-01T00:00:00Z",
            }

    def worker(input_data, context):
        roles.append(_worker_role(input_data))
        _write_analyst_result(input_data, context, prepare_change=False)
        return ToolResult(ok=True, output="critical analysis complete")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(
        tmp_path,
        _request(kind="read_only", risk="critical", complex=True),
        monkeypatch,
        approval_provider=ApprovalProvider(),
    )

    assert result.ok is True
    assert roles == ["analyst"]
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["ruleId"] == "route.critical_read_only_human_gate"
    assert trace["committed"] is False


def test_analyst_unknown_requires_request_revision_before_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        assert role == "analyst"
        _write_analyst_result(
            input_data,
            context,
            prepare_change=True,
            unknowns=["The requirement has two incompatible interpretations."],
        )
        return ToolResult(ok=True, output="analysis found an ambiguity")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(
        tmp_path,
        _request(risk="high", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is False
    assert roles == ["analyst"]
    assert target.read_text(encoding="utf-8") == "before"
    assert "analyst_unknowns_require_request_revision" in result.output
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["status"] == "failed"
    assert len(audits) == 1
    audit = audits[0]
    analyst_artifact = next(
        artifact
        for artifact in audit.artifacts
        if json.loads(artifact.content).get("resultType") == "AnalystHandoff.v1"
    )
    mutated_handoff = json.loads(analyst_artifact.content)
    mutated_handoff["report"]["unknowns"] = []
    tampered_artifacts = tuple(
        SchedulerArtifact(
            artifact.artifact_id,
            json.dumps(
                mutated_handoff,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
        if artifact.artifact_id == analyst_artifact.artifact_id
        else artifact
        for artifact in audit.artifacts
    )
    with pytest.raises(
        SchedulerAuditValidationError,
        match="Analyst terminal cause",
    ):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=tampered_artifacts,
            contract_mode="v2",
        )


def test_analyst_discovered_high_risk_requires_scheduler_reclassification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "adjudicator":
            _write_adjudication_result(input_data, context, decision="insufficient")
            return ToolResult(ok=True, output="adjudication inconclusive")
        assert role == "analyst"
        _write_analyst_result(
            input_data,
            context,
            prepare_change=True,
            risks=[
                {
                    "severity": "high",
                    "statement": "The declared medium-risk route is unsafe.",
                    "evidenceRefs": ["claim_route"],
                }
            ],
        )
        return ToolResult(ok=True, output="analysis raised the risk")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(tmp_path, _request(risk="medium", complex=True), monkeypatch)

    assert result.ok is False
    assert roles == ["analyst", "adjudicator"]
    assert target.read_text(encoding="utf-8") == "before"
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["status"] == "failed"
    assert trace["terminalReason"] == "adjudication_inconclusive"
    assert [
        transition["outcome"]
        for transition in trace["transitions"]
        if transition["stage"] == "analyst"
    ] == ["dispatched", "blocked"]
    assert [
        transition["outcome"]
        for transition in trace["transitions"]
        if transition["stage"] == "adjudicator"
    ] == ["dispatched", "rejected"]
    blocked = [
        transition
        for transition in trace["transitions"]
        if transition["stage"] == "analyst"
        and transition["outcome"] == "blocked"
    ]
    assert blocked[0]["errorCode"] == "analyst_risk_requires_reclassification"


def test_analyst_risk_conflict_adjudicated_continuation_resumes_the_locked_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []
    audits = []

    class AuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "adjudicator":
            _write_adjudication_result(input_data, context, decision="select_b")
            return ToolResult(ok=True, output="locked profile stands")
        if role == "analyst":
            _write_analyst_result(
                input_data,
                context,
                prepare_change=True,
                risks=[
                    {
                        "severity": "high",
                        "statement": "The declared medium-risk route is unsafe.",
                        "evidenceRefs": ["claim_route"],
                    }
                ],
            )
            return ToolResult(ok=True, output="analysis raised the risk")
        if role == "executor":
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
            _write_executor_declaration(input_data, context)
            return ToolResult(ok=True, output="bounded execution complete")
        _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output="strict review approved")

    def verifier(self, **request):
        roles.append("verifier")
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=AuditSink(),
        commit_audit_waived=True,
    )

    assert result.ok is True
    assert roles == ["analyst", "adjudicator", "executor", "verifier", "reviewer"]
    assert target.read_text(encoding="utf-8") == "after"
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["status"] == "completed"
    assert trace["committed"] is True
    assert [
        transition["outcome"]
        for transition in trace["transitions"]
        if transition["stage"] == "adjudicator"
    ] == ["dispatched", "completed"]
    analyst_outcomes = [
        transition["outcome"]
        for transition in trace["transitions"]
        if transition["stage"] == "analyst"
    ]
    assert analyst_outcomes == ["dispatched", "blocked", "completed"]
    assert len(audits) == 1


def test_analyst_risk_conflict_adjudicated_stop_terminates_before_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "adjudicator":
            _write_adjudication_result(input_data, context, decision="select_a")
            return ToolResult(ok=True, output="reclassification required")
        assert role == "analyst"
        _write_analyst_result(
            input_data,
            context,
            prepare_change=True,
            risks=[
                {
                    "severity": "critical",
                    "statement": "The route would delete production state.",
                    "evidenceRefs": ["claim_route"],
                }
            ],
        )
        return ToolResult(ok=True, output="analysis raised the risk")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(tmp_path, _request(risk="medium", complex=True), monkeypatch)

    assert result.ok is False
    assert roles == ["analyst", "adjudicator"]
    assert target.read_text(encoding="utf-8") == "before"
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["status"] == "failed"
    assert trace["terminalReason"] == "adjudicated_reclassification_required"


def test_analyst_phase_failure_retries_with_exact_reason_not_port_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    analyst_attempts = 0

    def worker(input_data, _context):
        nonlocal analyst_attempts
        assert _worker_role(input_data) == "analyst"
        analyst_attempts += 1
        return ToolResult(ok=False, output="analyst provider failed")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
    )

    assert result.ok is False
    assert analyst_attempts == 2
    assert target.read_text(encoding="utf-8") == "before"
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["terminalReason"] == "plan_phase_failed"
    assert "stage_port_failed" not in result.output
    assert [
        transition["outcome"]
        for transition in trace["transitions"]
        if transition["stage"] == "analyst"
    ] == ["dispatched", "failed", "retrying", "dispatched", "failed"]


def test_empty_analyst_step_scope_fails_before_executor_and_is_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        roles.append(_worker_role(input_data))
        _write_analyst_result(
            input_data,
            context,
            prepare_change=True,
            step_allowed_paths=[],
        )
        return ToolResult(ok=True, output="invalid empty step scope")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is False
    assert roles == ["analyst", "analyst"]
    assert target.read_text(encoding="utf-8") == "before"
    assert len(audits) == 1
    assert audits[0].trace["terminalReason"] == "analyst_result_invalid"
    assert (
        project_scheduler_audit(
            profile=audits[0].profile,
            plan=audits[0].plan,
            trace=audits[0].trace,
            artifacts=audits[0].artifacts,
            contract_mode="v2",
        ).trace
        == audits[0].trace
    )


def test_high_risk_failure_before_human_stage_is_durably_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def failed_analyst(input_data, _context):
        assert _worker_role(input_data) == "analyst"
        return ToolResult(ok=False, output="analyst provider unavailable")

    monkeypatch.setattr(task_module, "_run", failed_analyst)

    result = _run(
        tmp_path,
        _request(risk="high", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "before"
    assert len(audits) == 1
    audit = audits[0]
    assert all(
        transition["stage"] != "human" for transition in audit.trace["transitions"]
    )
    assert all(
        json.loads(artifact.content).get("resultType")
        not in {"HumanApprovalRequest.v1", "HumanApproval.v1"}
        for artifact in audit.artifacts
    )
    assert (
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=audit.artifacts,
            contract_mode="v2",
        ).trace
        == audit.trace
    )

    forged_trace = json.loads(json.dumps(audit.trace))
    forged_terminal = forged_trace["transitions"][-1]
    forged_terminal["errorCode"] = "made_up_error"
    forged_trace["terminalReason"] = "made_up_error"
    receipt_id = str(forged_terminal["artifactId"])
    forged_artifacts = tuple(
        SchedulerArtifact(
            artifact.artifact_id,
            json.dumps(
                {
                    **json.loads(artifact.content),
                    "errorCode": "made_up_error",
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
        if artifact.artifact_id == receipt_id
        else artifact
        for artifact in audit.artifacts
    )
    with pytest.raises(
        SchedulerAuditValidationError,
        match="terminal cause is outside the closed policy",
    ):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=forged_trace,
            artifacts=forged_artifacts,
            contract_mode="v2",
        )

    typed_cause_trace = json.loads(json.dumps(audit.trace))
    typed_cause_terminal = typed_cause_trace["transitions"][-1]
    typed_cause_terminal["errorCode"] = "analyst_result_failed"
    typed_cause_trace["terminalReason"] = "analyst_result_failed"
    typed_cause_artifacts = tuple(
        SchedulerArtifact(
            artifact.artifact_id,
            json.dumps(
                {
                    **json.loads(artifact.content),
                    "errorCode": "analyst_result_failed",
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
        if artifact.artifact_id == receipt_id
        else artifact
        for artifact in audit.artifacts
    )
    with pytest.raises(
        SchedulerAuditValidationError,
        match="stage receipt cause is inconsistent",
    ):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=typed_cause_trace,
            artifacts=typed_cause_artifacts,
            contract_mode="v2",
        )

    forged_retry_trace = json.loads(json.dumps(audit.trace))
    retry_transition = next(
        transition
        for transition in forged_retry_trace["transitions"]
        if transition["outcome"] == "retrying"
    )
    retry_transition["errorCode"] = "analyst_result_invalid"
    with pytest.raises(
        SchedulerAuditValidationError,
        match="retry cause is inconsistent",
    ):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=forged_retry_trace,
            artifacts=audit.artifacts,
            contract_mode="v2",
        )


def test_scheduler_port_failure_cannot_claim_semantic_role_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def unavailable_worker(_input_data, _context):
        raise RuntimeError("worker port unavailable")

    monkeypatch.setattr(task_module, "_run", unavailable_worker)

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is False
    assert len(audits) == 1
    assert audits[0].trace["terminalReason"] == "stage_port_failed"
    forged_trace = json.loads(json.dumps(audits[0].trace))
    forged_trace["transitions"][-1]["errorCode"] = "analyst_result_failed"
    forged_trace["terminalReason"] = "analyst_result_failed"
    with pytest.raises(
        SchedulerAuditValidationError,
        match="semantic cause requires role authority",
    ):
        project_scheduler_audit(
            profile=audits[0].profile,
            plan=audits[0].plan,
            trace=forged_trace,
            artifacts=audits[0].artifacts,
            contract_mode="v2",
        )


@pytest.mark.parametrize(
    (
        "fault_stage",
        "expected_failure_stage",
        "expected_terminal_reason",
        "expected_roles",
        "expected_verifier_calls",
    ),
    [
        pytest.param(
            "executor",
            "executor",
            "stage_port_failed",
            ("analyst", "executor"),
            0,
            id="executor-worker-runtime-error",
        ),
        pytest.param(
            "reviewer",
            "reviewer",
            "stage_port_failed",
            ("analyst", "executor", "reviewer"),
            1,
            id="reviewer-worker-runtime-error",
        ),
        pytest.param(
            "verifier",
            "verifier",
            "verifier_unavailable",
            ("analyst", "executor"),
            1,
            id="deterministic-verifier-runtime-error",
        ),
    ],
)
def test_structured_stage_adapter_faults_fail_closed_with_replayable_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_stage: str,
    expected_failure_stage: str,
    expected_terminal_reason: str,
    expected_roles: tuple[str, ...],
    expected_verifier_calls: int,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []
    verifier_calls = 0
    commit_ready_records = []
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, ready):
            commit_ready_records.append(ready)
            return True

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "isolated change",
                encoding="utf-8",
            )
            if fault_stage == "executor":
                raise RuntimeError("executor adapter failed")
            _write_executor_declaration(input_data, context)
        else:
            assert role == "reviewer"
            if fault_stage == "reviewer":
                raise RuntimeError("reviewer adapter failed")
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    def verifier(_self, **request):
        nonlocal verifier_calls
        verifier_calls += 1
        if fault_stage == "verifier":
            raise RuntimeError("deterministic verifier infrastructure failed")
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "before"
    assert tuple(roles) == expected_roles
    assert verifier_calls == expected_verifier_calls
    assert commit_ready_records == []
    assert len(audits) == 1

    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["status"] == "failed"
    assert trace["committed"] is False
    assert trace["terminalReason"] == expected_terminal_reason
    assert trace["transitions"][-1]["stage"] == expected_failure_stage
    assert trace["transitions"][-1]["errorCode"] == expected_terminal_reason
    assert all(
        transition["stage"] != "commit" for transition in trace["transitions"]
    )

    audit = audits[0]
    assert audit.trace == trace
    assert (
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=audit.artifacts,
            contract_mode="v2",
        ).trace
        == trace
    )


def test_complex_read_only_request_uses_one_analyst_without_transaction_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    request = _request(kind="read_only")
    request["profile"]["complex"] = True
    roles = []

    def worker(input_data, context):
        roles.append(input_data["agent_type"])
        _write_analyst_result(input_data, context, prepare_change=False)
        return ToolResult(ok=True, output="evidence-backed analysis")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(tmp_path, request, monkeypatch)

    assert result.ok is True
    assert roles == ["plan"]
    assert '"resultType": "AnalystReport.v1"' in result.output
    assert "evidence-backed analysis" not in result.output
    projected = extract_subagent_result(result.output)
    assert projected is not None
    assert projected["summary"] == "The bounded route is supported."
    assert projected["files"] == [{"path": "README.md", "action": "read"}]
    assert "error[review_not_run]" not in projected["risks"]
    assert projected["verification"] == {"status": "not_run", "checks": []}


def test_medium_complex_request_runs_strict_full_chain_in_role_order_and_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []
    audits = []

    class AuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
            return ToolResult(ok=True, output="strict analysis complete")
        if role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
            return ToolResult(ok=True, output="bounded execution complete")
        _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output="strict review approved")

    def verifier(self, **request):
        roles.append("verifier")
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=AuditSink(),
        commit_audit_waived=True,
    )

    assert result.ok is True
    assert roles == ["analyst", "executor", "verifier", "reviewer"]
    assert target.read_text(encoding="utf-8") == "after"
    assert '"verdict": "approved"' in result.output
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["taskId"] == "task_structured_route"
    assert trace["ruleId"] == "route.reviewed_change"
    assert trace["status"] == "completed"
    assert trace["committed"] is True
    assert len(audits) == 1
    assert audits[0].profile.risk.value == "medium"
    assert audits[0].contract_mode == "v2"
    assert audits[0].trace == trace
    assert any(
        artifact.artifact_id.endswith("_frozen_diff")
        for artifact in audits[0].artifacts
    )
    assignment_artifacts = [
        json.loads(artifact.content)
        for artifact in audits[0].artifacts
        if json.loads(artifact.content).get("resultType") == "AssignmentArtifact.v1"
    ]
    assert [artifact["assignment"]["role"] for artifact in assignment_artifacts] == [
        "analyst",
        "executor",
        "reviewer",
    ]
    assert all(
        artifact["attemptId"].startswith("attempt_")
        for artifact in assignment_artifacts
    )
    assert any(
        json.loads(artifact.content).get("resultType") == "EvidenceSnapshot.v1"
        for artifact in audits[0].artifacts
    )
    assert [
        item["stage"] for item in trace["transitions"] if item["outcome"] == "completed"
    ] == ["analyst", "executor", "verifier", "reviewer", "commit"]


@pytest.mark.parametrize(
    "tampered_authority",
    ["analyst", "executor", "verification_evidence"],
)
def test_offline_audit_replays_completed_authority_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered_authority: str,
) -> None:
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    audits = []

    class AuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=AuditSink(),
        commit_audit_waived=True,
    )
    assert result.ok is True
    audit = audits[0]
    if tampered_authority in {"analyst", "executor"}:
        target_id = next(
            str(transition["artifactId"])
            for transition in audit.trace["transitions"]
            if transition["stage"] == tampered_authority
            and transition["outcome"] == "completed"
        )
    else:
        verification = next(
            json.loads(artifact.content)
            for artifact in audit.artifacts
            if json.loads(artifact.content).get("resultType") == "VerificationResult.v1"
        )
        target_id = str(verification["checks"][0]["artifactRef"])
    tampered_artifacts = tuple(
        SchedulerArtifact(
            artifact.artifact_id,
            '{"fabricated":true}',
        )
        if artifact.artifact_id == target_id
        else artifact
        for artifact in audit.artifacts
    )

    with pytest.raises(SchedulerAuditValidationError):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=tampered_artifacts,
            contract_mode="v2",
        )


def test_reviewer_retry_commits_with_audit_bound_to_second_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    reviewer_result_keys: list[str] = []
    ready_records = []
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, ready):
            ready_records.append(ready)
            return True

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            reviewer_result_keys.append(input_data["_result_key"])
            if len(reviewer_result_keys) == 1:
                context._subagent_mailbox.write(
                    input_data["_result_key"],
                    json.dumps({"resultVersion": 1}),
                    author="reviewer",
                )
            else:
                _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "after"
    assert len(reviewer_result_keys) == 2
    assert len(ready_records) == len(audits) == 1
    assert '"auditStatus": "persisted"' in result.output

    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["committed"] is True
    reviewer_transitions = [
        transition
        for transition in trace["transitions"]
        if transition["stage"] == "reviewer"
    ]
    assert [item["outcome"] for item in reviewer_transitions] == [
        "dispatched",
        "failed",
        "retrying",
        "dispatched",
        "completed",
    ]

    artifact_by_id = {
        artifact.artifact_id: artifact for artifact in audits[0].artifacts
    }
    reviewer_assignments = {
        document["attemptId"]: document
        for artifact in audits[0].artifacts
        if (
            isinstance(document := json.loads(artifact.content), dict)
            and document.get("resultType") == "AssignmentArtifact.v1"
            and document.get("stage") == "reviewer"
        )
    }
    dispatched_attempts = [
        item["attemptId"]
        for item in reviewer_transitions
        if item["outcome"] == "dispatched"
    ]
    assert set(reviewer_assignments) == set(dispatched_attempts)

    completed_transition = reviewer_transitions[-1]
    second_assignment = reviewer_assignments[dispatched_attempts[1]]
    assert completed_transition["attemptId"] == second_assignment["attemptId"]
    assert completed_transition["artifactId"] == reviewer_result_keys[1]
    approved_artifact = artifact_by_id[reviewer_result_keys[1]]
    approved_review = json.loads(approved_artifact.content)
    assert (
        approved_review["assignmentId"]
        == second_assignment["assignment"]["assignmentId"]
    )
    assert (
        ready_records[0].review_digest
        == "sha256:"
        + hashlib.sha256(approved_artifact.content.encode("utf-8")).hexdigest()
    )


def test_maximum_v2_evidence_and_retry_shape_stays_below_artifact_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text(
        "".join(f"baseline line {index}\n" for index in range(1, 65)),
        encoding="utf-8",
    )
    analyst_attempts = 0
    reviewer_attempts = 0
    audits = []

    class ApprovalProvider:
        def consume_approval(self, **request):
            return {
                "approvalVersion": 1,
                "approvalRequestId": request["approval_request_id"],
                "taskId": request["task_id"],
                "requestDigest": request["request_digest"],
                "baseDigest": request["base_digest"],
                "analystDigest": request["analyst_digest"],
                "decision": "approved",
                "approvedBy": "artifact_budget_reviewer",
                "expiresAt": "2999-01-01T00:00:00Z",
            }

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def write_maximum_analyst_result(input_data, context):
        assignment = _prompt_json(input_data["prompt"], "ASSIGNMENT")
        report = {
            "resultVersion": 1,
            "assignmentId": assignment["assignmentId"],
            "status": "completed",
            "findings": [
                {
                    "claimId": f"claim_budget_{group}",
                    "statement": f"Evidence group {group} is frozen.",
                    "evidence": [
                        {
                            "path": "allowed.txt",
                            "lineStart": line,
                            "lineEnd": line,
                        }
                        for line in range(group * 16 + 1, group * 16 + 17)
                    ],
                    "confidence": "high",
                }
                for group in range(4)
            ],
            "unknowns": [],
            "risks": [],
            "implementationOrderCandidate": {
                "required": True,
                "steps": [
                    {
                        "id": "s_budget",
                        "action": "Apply the bounded change.",
                        "allowedPaths": ["allowed.txt"],
                    }
                ],
                "verificationRequests": [],
            },
            "ballot": None,
        }
        context._subagent_mailbox.write(
            input_data["_result_key"],
            json.dumps(report),
            author="analyst",
        )

    def worker(input_data, context):
        nonlocal analyst_attempts, reviewer_attempts
        role = _worker_role(input_data)
        if role == "analyst":
            analyst_attempts += 1
            if analyst_attempts == 1:
                context._subagent_mailbox.write(
                    input_data["_result_key"],
                    json.dumps({"resultVersion": 1}),
                    author="analyst",
                )
            else:
                write_maximum_analyst_result(input_data, context)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after\n",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            reviewer_attempts += 1
            if reviewer_attempts == 1:
                context._subagent_mailbox.write(
                    input_data["_result_key"],
                    json.dumps({"resultVersion": 1}),
                    author="reviewer",
                )
            else:
                assignment = _prompt_json(input_data["prompt"], "ASSIGNMENT")
                review_refs = [
                    ref
                    for ref in assignment["inputRefs"]
                    if ref.startswith("artifact_acceptance_")
                    or ref.startswith("artifact_verification_")
                    or ref.endswith("_frozen_diff")
                ]
                _write_reviewer_result(
                    input_data,
                    context,
                    evidence_refs=review_refs,
                )
        return ToolResult(ok=True, output=f"{role} complete")

    request = _request(risk="high", complex=True)
    request["verificationChecks"] = [
        {
            "checkId": f"check_budget_{index}",
            "kind": "tests",
            "argv": ["pytest", "-q", f"tests/test_budget_{index}.py"],
            "criterionIds": ["ac1"],
        }
        for index in range(40)
    ]
    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **verification_request: _verification_result(verification_request),
    )

    result = _run(
        tmp_path,
        request,
        monkeypatch,
        event_sink=DurableSink(),
        approval_provider=ApprovalProvider(),
        central_enforced=True,
    )

    assert result.ok is True, result.output
    assert target.read_text(encoding="utf-8") == "after\n"
    assert analyst_attempts == reviewer_attempts == 2
    assert len(audits) == 1
    assert len(audits[0].artifacts) == (MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW - 2)
    assert audits[0].trace["committed"] is True


def test_long_retry_narrative_preserves_both_authority_artifacts_and_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    reviewer_attempts = 0
    long_narrative = "retry narration must not displace authority\n" * 10_000
    # Stay above the minimum lifecycle-complete authority projection while
    # forcing redundant retry chatter out of the full 13-transition trace.
    output_cap = 6_300

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, _audit):
            return True

    def worker(input_data, context):
        nonlocal reviewer_attempts
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            reviewer_attempts += 1
            if reviewer_attempts == 1:
                return ToolResult(ok=False, output=long_narrative)
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=long_narrative)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        workflow_finalization_module,
        "_WORKFLOW_OUTPUT_MAX_CHARS",
        output_cap,
    )
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    trace = extract_scheduler_trace(result.output)
    projected = extract_subagent_result(result.output)
    assert result.ok is True
    assert reviewer_attempts == 2
    assert target.read_text(encoding="utf-8") == "after"
    assert trace is not None
    # The durable audit keeps the failed attempt. The bounded public trace may
    # discard that entire diagnostic lifecycle, but never a partial lifecycle.
    assert not any(
        transition["outcome"] == "retrying" for transition in trace["transitions"]
    )
    assert projected is not None
    assert projected["outcome"] == "completed"
    assert "=== WORKFLOW FINALIZATION v1 ===" in result.output
    assert '"changesCommitted": true' in result.output
    assert '"finalizationStatus": "completed"' in result.output
    assert '"traceProjection": "compacted"' in result.output
    assert len(result.output) <= output_cap


def test_cancellation_from_injected_worker_propagates_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []
    cancellation = TurnCancellationRequested("turn_" + "c" * 32)

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
            return ToolResult(ok=True, output="analyst complete")
        if role == "executor":
            raise cancellation
        pytest.fail("Verifier and Reviewer must not run after cancellation")

    monkeypatch.setattr(task_module, "_run", worker)

    with pytest.raises(TurnCancellationRequested) as raised:
        _run(tmp_path, _request(), monkeypatch)

    assert raised.value is cancellation
    assert roles == ["analyst", "executor"]
    assert target.read_text(encoding="utf-8") == "before"


def test_multiple_retries_with_maximum_task_id_keep_public_authority_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    attempts = {"analyst": 0, "reviewer": 0}

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, _audit):
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role in attempts:
            attempts[role] += 1
            if attempts[role] == 1:
                return ToolResult(ok=False, output=f"{role} retry")
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    request = _request(risk="medium", complex=True)
    request["profile"]["taskId"] = "task_" + "x" * 120
    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **verification_request: _verification_result(verification_request),
    )

    result = _run(
        tmp_path,
        request,
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is True
    assert attempts == {"analyst": 2, "reviewer": 2}
    assert target.read_text(encoding="utf-8") == "after"
    assert extract_scheduler_trace(result.output) is not None
    assert extract_subagent_result(result.output) is not None
    assert '"changesCommitted": true' in result.output
    assert len(result.output) <= workflow_finalization_module._WORKFLOW_OUTPUT_MAX_CHARS


def test_reviewer_cannot_approve_without_mapped_verification_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            assignment = _prompt_json(input_data["prompt"], "ASSIGNMENT")
            _write_reviewer_result(
                input_data,
                context,
                evidence_refs=[assignment["inputRefs"][-1]],
            )
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
    )

    assert result.ok is False
    assert "review_evidence_binding_invalid" in result.output
    assert target.read_text(encoding="utf-8") == "before"


def test_failed_verifier_artifact_remains_replayable_and_tamper_evident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        else:
            assert role == "executor"
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    def failed_verifier(_self, **request):
        evidence: list[tuple[str, dict[str, object]]] = []
        original_sink = request["evidence_sink"]
        projected = _verification_result(
            {
                **request,
                "evidence_sink": lambda artifact_id, document: evidence.append(
                    (artifact_id, document)
                ),
            }
        )
        projected["status"] = "failed"
        projected["checks"][0]["status"] = "failed"
        projected["checks"][0]["exitCode"] = 1
        artifact_id, document = evidence[0]
        document["status"] = "failed"
        document["exitCode"] = 1
        original_sink(artifact_id, document)
        return projected

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", failed_verifier)

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "before"
    assert len(audits) == 1
    audit = audits[0]
    verifier_transition = next(
        transition
        for transition in audit.trace["transitions"]
        if transition["stage"] == "verifier" and transition["outcome"] == "inconclusive"
    )
    verification_artifact_id = str(verifier_transition["artifactId"])
    tampered_artifacts = tuple(
        SchedulerArtifact(
            artifact.artifact_id,
            '{"fabricated":true}',
        )
        if artifact.artifact_id == verification_artifact_id
        else artifact
        for artifact in audit.artifacts
    )

    with pytest.raises(
        SchedulerAuditValidationError,
        match="repair previous evidence|Verifier terminal artifact",
    ):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=tampered_artifacts,
            contract_mode="v2",
        )

    verification_document = json.loads(
        next(
            artifact.content
            for artifact in audit.artifacts
            if artifact.artifact_id == verification_artifact_id
        )
    )
    evidence_artifact_id = verification_document["checks"][0]["artifactRef"]
    evidence_document = json.loads(
        next(
            artifact.content
            for artifact in audit.artifacts
            if artifact.artifact_id == evidence_artifact_id
        )
    )
    verification_document["status"] = "passed"
    verification_document["checks"][0]["status"] = "passed"
    verification_document["checks"][0]["exitCode"] = 0
    evidence_document["status"] = "passed"
    evidence_document["exitCode"] = 0

    def canonical(document: dict[str, object]) -> str:
        return json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    cause_tampered_artifacts = tuple(
        SchedulerArtifact(artifact.artifact_id, canonical(verification_document))
        if artifact.artifact_id == verification_artifact_id
        else SchedulerArtifact(artifact.artifact_id, canonical(evidence_document))
        if artifact.artifact_id == evidence_artifact_id
        else artifact
        for artifact in audit.artifacts
    )
    with pytest.raises(
        SchedulerAuditValidationError,
        match="repairable failed evidence|Verifier terminal cause",
    ):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=cause_tampered_artifacts,
            contract_mode="v2",
        )

    request_artifact = next(
        artifact
        for artifact in audit.artifacts
        if json.loads(artifact.content).get("resultType")
        == "WorkflowRequestArtifact.v1"
    )
    mismatched_request = json.loads(request_artifact.content)
    mismatched_request["request"]["profile"]["risk"] = "critical"
    request_payload = json.dumps(
        {
            "objectiveDigest": mismatched_request["objectiveDigest"],
            "request": mismatched_request["request"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    mismatched_request["requestDigest"] = (
        "sha256:" + hashlib.sha256(request_payload).hexdigest()
    )
    request_tampered_artifacts = tuple(
        SchedulerArtifact(
            artifact.artifact_id,
            json.dumps(
                mismatched_request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        if artifact.artifact_id == request_artifact.artifact_id
        else artifact
        for artifact in audit.artifacts
    )
    with pytest.raises(
        SchedulerAuditValidationError,
        match="review routing authority is missing",
    ):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=request_tampered_artifacts,
            contract_mode="v2",
        )

    assignment_artifact = next(
        artifact
        for artifact in audit.artifacts
        if (
            (document := json.loads(artifact.content)).get("resultType")
            == "AssignmentArtifact.v1"
            and document.get("stage") == "analyst"
        )
    )
    mismatched_assignment = json.loads(assignment_artifact.content)
    mismatched_assignment["assignment"]["scope"]["readRoots"] = ["allowed.txt"]
    mismatched_assignment["assignment"]["risk"] = "low"
    assignment_tampered_artifacts = tuple(
        SchedulerArtifact(
            artifact.artifact_id,
            json.dumps(
                mismatched_assignment,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        if artifact.artifact_id == assignment_artifact.artifact_id
        else artifact
        for artifact in audit.artifacts
    )
    with pytest.raises(
        SchedulerAuditValidationError,
        match="bound to WorkflowRequest",
    ):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=assignment_tampered_artifacts,
            contract_mode="v2",
        )


def test_malformed_executor_result_is_replaced_by_exact_failure_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    audits = []
    executor_result_key: str | None = None

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        nonlocal executor_result_key
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        else:
            assert role == "executor"
            executor_result_key = input_data["_result_key"]
            # Semantic preflight now rejects this at the note boundary. The
            # returned worker still has no accepted declaration, so its final
            # trace must retain a real Scheduler failure receipt.
            with pytest.raises(SchedulerArtifactError, match="invalid Executor declaration"):
                context._subagent_mailbox.write(
                    executor_result_key,
                    '{"fabricated":true}',
                    author="executor",
                )
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "before"
    assert executor_result_key is not None
    assert len(audits) == 1
    audit = audits[0]
    terminal = next(
        transition
        for transition in audit.trace["transitions"]
        if transition["stage"] == "executor" and transition["outcome"] == "failed"
    )
    receipt = next(
        json.loads(artifact.content)
        for artifact in audit.artifacts
        if artifact.artifact_id == terminal["artifactId"]
    )
    assert receipt["resultType"] == "SchedulerStageReceipt.v1"
    assert terminal["artifactId"] != executor_result_key

    forged_trace = json.loads(json.dumps(audit.trace))
    forged_terminal = next(
        transition
        for transition in forged_trace["transitions"]
        if transition["stage"] == "executor" and transition["outcome"] == "failed"
    )
    forged_terminal["artifactId"] = executor_result_key
    with pytest.raises(
        SchedulerAuditValidationError,
        match="unsealed artifact",
    ):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=forged_trace,
            artifacts=audit.artifacts,
            contract_mode="v2",
        )


@pytest.mark.parametrize(
    ("failure_mode", "audit_status"),
    [
        ("false", "persistence_failed"),
        ("interrupt", "projection_or_persistence_failed"),
    ],
)
def test_committed_change_reports_audit_persistence_failure_without_lying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
    audit_status: str,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    class FailingAuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, _audit):
            if failure_mode == "interrupt":
                raise KeyboardInterrupt("audit recorder interrupted")
            return False

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=FailingAuditSink(),
        commit_audit_waived=True,
    )

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "after"
    assert '"changesCommitted": true' in result.output
    assert f'"auditStatus": "{audit_status}"' in result.output
    assert "workflow_finalization_incomplete" in result.output


@pytest.mark.parametrize("recorder_mode", ["missing", "false"])
def test_production_commit_ready_persistence_failure_blocks_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorder_mode: str,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    observed_parent_states: list[str] = []

    class CommitReadySink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, _audit):
            return True

        if recorder_mode == "false":

            def record_scheduler_commit_ready(self, _record):
                observed_parent_states.append(target.read_text(encoding="utf-8"))
                return False

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=CommitReadySink(),
        central_enforced=True,
    )

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "before"
    assert "commit_ready_persistence_failed" in result.output
    expected_status = (
        "recorder_missing" if recorder_mode == "missing" else "persistence_failed"
    )
    assert f'"commitReadyStatus": "{expected_status}"' in result.output
    assert observed_parent_states == ([] if recorder_mode == "missing" else ["before"])


def test_missing_commit_ready_recorder_fails_closed_without_enforced_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Non-production mode no longer commits with zero persisted audit: a
    # missing commit-ready recorder refuses promotion by default, and only an
    # explicit waiver reopens the lenient path.
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
    )

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "before"
    assert "commit_ready_persistence_failed" in result.output
    assert '"commitReadyStatus": "recorder_missing"' in result.output

    waived = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        commit_audit_waived=True,
    )

    assert waived.ok is True
    assert target.read_text(encoding="utf-8") == "after"
    assert '"commitReadyStatus": "waived"' in waived.output


def test_production_commit_ready_is_sealed_before_parent_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    ready_records = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, record):
            assert target.read_text(encoding="utf-8") == "before"
            ready_records.append(record)
            return True

        def record_scheduler_audit(self, _audit):
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "after"
    assert len(ready_records) == 1
    assert ready_records[0].patch_digest.startswith("sha256:")
    assert '"commitReadyStatus": "persisted"' in result.output


def test_deadline_after_commit_ready_seal_reports_persisted_without_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    now = [0.0]
    ready_records = []
    audits = []

    class DeadlineSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, ready):
            ready_records.append(ready)
            now[0] = 10.0
            return True

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    original_execute = CentralScheduler.execute

    def execute_with_clock(self, *args, **kwargs):
        kwargs["clock"] = lambda: now[0]
        return original_execute(self, *args, **kwargs)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(CentralScheduler, "execute", execute_with_clock)
    monkeypatch.setattr(task_deadline_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DeadlineSink(),
        central_enforced=True,
        task_deadline_monotonic=5.0,
    )

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "before"
    assert len(ready_records) == 1
    assert len(audits) == 1
    assert audits[0].trace["status"] == "timed_out"
    assert audits[0].trace["terminalReason"] == "global_deadline_exceeded"
    assert '"commitReadyStatus": "persisted"' in result.output
    assert '"auditStatus": "persisted"' in result.output
    assert '"changesCommitted": false' in result.output
    assert '"finalizationStatus": "timed_out"' in result.output
    assert "global_deadline_exceeded" in result.output

    ready = ready_records[0]
    bad_ready = project_scheduler_commit_ready(
        workflow_id=ready.workflow_id,
        task_id=ready.task_id,
        rule_id=ready.rule_id,
        request_digest=ready.request_digest,
        base_digest=ready.base_digest,
        patch_digest="sha256:" + "0" * 64,
        verification_digest=ready.verification_digest,
        review_digest=ready.review_digest,
        approval_digest=ready.approval_digest,
        artifact_set_digest=ready.artifact_set_digest,
    )
    journal_workspace = tmp_path.parent / f"{tmp_path.name}-timeout-journal"
    journal_workspace.mkdir()
    journal = RunJournal(
        journal_workspace,
        data_dir=tmp_path.parent / f"{tmp_path.name}-timeout-data",
    )
    record = journal.create_run(title="timeout binding", source="headless")
    journal.transition(record.id, "running")
    assert journal.record_scheduler_commit_ready(record.id, bad_ready) is True
    with pytest.raises(RunJournalValidationError, match="audit"):
        journal.record_scheduler_audit(record.id, audits[0])

    matching_workspace = tmp_path.parent / f"{tmp_path.name}-matching-journal"
    matching_workspace.mkdir()
    matching_journal = RunJournal(
        matching_workspace,
        data_dir=tmp_path.parent / f"{tmp_path.name}-matching-data",
    )
    matching_record = matching_journal.create_run(
        title="matching timeout binding",
        source="headless",
    )
    matching_journal.transition(matching_record.id, "running")
    assert (
        matching_journal.record_scheduler_commit_ready(
            matching_record.id,
            ready,
        )
        is True
    )
    matching_journal.record_scheduler_audit(matching_record.id, audits[0])
    assert (
        matching_journal.get_scheduler_audit(
            matching_record.id,
            audits[0].workflow_id,
        )
        == audits[0]
    )


def test_production_missing_final_audit_recorder_blocks_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    commit_ready_calls = []

    class IncompleteSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, ready):
            commit_ready_calls.append(ready)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=IncompleteSink(),
        central_enforced=True,
    )

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "before"
    assert commit_ready_calls == []
    assert "commit_ready_persistence_failed" in result.output
    assert '"commitReadyStatus": "recorder_missing"' in result.output


def test_production_run_journal_joins_commit_ready_to_final_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    data_dir = tmp_path.parent / f"{tmp_path.name}-run-data"

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="durable structured workflow",
        journal_factory=lambda resolved: RunJournal(
            resolved,
            data_dir=data_dir,
        ),
    ) as observation:
        result = _run(
            tmp_path,
            _request(risk="medium", complex=True),
            monkeypatch,
            event_sink=observation,
            central_enforced=True,
        )
        run_id = observation.run_id

    assert result.ok is True
    assert run_id is not None
    assert target.read_text(encoding="utf-8") == "after"
    journal = RunJournal(tmp_path, data_dir=data_dir)
    summaries = journal.list_scheduler_audits(run_id)
    assert len(summaries) == 1
    assert summaries[0].status == "completed"
    assert summaries[0].committed is True
    workflow_id = summaries[0].workflow_id
    ready = journal.get_scheduler_commit_ready(run_id, workflow_id)
    audit = journal.get_scheduler_audit(run_id, workflow_id)
    assert ready is not None
    assert audit is not None
    assert ready.patch_digest == next(
        str(json.loads(artifact.content)["patchDigest"])
        for artifact in audit.artifacts
        if json.loads(artifact.content).get("resultType") == "VerificationResult.v1"
    )

    ready_paths = list(data_dir.rglob("commit-ready.json"))
    assert len(ready_paths) == 1
    ready_path = ready_paths[0]
    tampered_ready = project_scheduler_commit_ready(
        workflow_id=ready.workflow_id,
        task_id=ready.task_id,
        rule_id=ready.rule_id,
        request_digest=ready.request_digest,
        base_digest="sha256:" + "0" * 64,
        patch_digest=ready.patch_digest,
        verification_digest=ready.verification_digest,
        review_digest=ready.review_digest,
        approval_digest=ready.approval_digest,
        artifact_set_digest=ready.artifact_set_digest,
    )
    ready_path.write_text(
        json.dumps(
            tampered_ready.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RunJournalStorageError, match="audit"):
        journal.get_scheduler_audit(run_id, workflow_id)

    ready_path.write_text(
        json.dumps(
            ready.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    ready_path.unlink()
    with pytest.raises(RunJournalStorageError, match="audit"):
        journal.list_scheduler_audits(run_id)


def test_final_audit_rejects_mismatched_commit_ready_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    ready_records = []
    audits = []

    class CollectingSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, ready):
            ready_records.append(ready)
            return True

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=CollectingSink(),
        central_enforced=True,
    )
    assert result.ok is True
    assert len(ready_records) == len(audits) == 1
    ready = ready_records[0]
    bad_ready = project_scheduler_commit_ready(
        workflow_id=ready.workflow_id,
        task_id=ready.task_id,
        rule_id=ready.rule_id,
        request_digest=ready.request_digest,
        base_digest=ready.base_digest,
        patch_digest=ready.patch_digest,
        verification_digest=ready.verification_digest,
        review_digest="sha256:" + "0" * 64,
        approval_digest=ready.approval_digest,
        artifact_set_digest=ready.artifact_set_digest,
    )

    journal_workspace = tmp_path.parent / f"{tmp_path.name}-mismatch-workspace"
    journal_workspace.mkdir()
    data_dir = tmp_path.parent / f"{tmp_path.name}-mismatch-data"
    journal = RunJournal(journal_workspace, data_dir=data_dir)
    record = journal.create_run(title="mismatch", source="headless")
    journal.transition(record.id, "running")
    assert journal.record_scheduler_commit_ready(record.id, bad_ready) is True

    with pytest.raises(RunJournalValidationError, match="audit"):
        journal.record_scheduler_audit(record.id, audits[0])

    verification_document = next(
        json.loads(artifact.content)
        for artifact in audits[0].artifacts
        if json.loads(artifact.content).get("resultType") == "VerificationResult.v1"
    )
    verification_document["verificationId"] = "verification_off_trace"
    off_trace_verification = SchedulerArtifact(
        "artifact_verification_off_trace_result",
        json.dumps(
            verification_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    extended_artifacts = (*audits[0].artifacts, off_trace_verification)
    extended_audit = project_scheduler_audit(
        profile=audits[0].profile,
        plan=audits[0].plan,
        trace=audits[0].trace,
        artifacts=extended_artifacts,
        contract_mode="v2",
    )
    artifact_index = [
        {
            "artifactId": artifact.artifact_id,
            "contentDigest": "sha256:"
            + hashlib.sha256(artifact.content.encode("utf-8")).hexdigest(),
        }
        for artifact in extended_artifacts
    ]
    artifact_set_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                artifact_index,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    off_trace_ready = project_scheduler_commit_ready(
        workflow_id=ready.workflow_id,
        task_id=ready.task_id,
        rule_id=ready.rule_id,
        request_digest=ready.request_digest,
        base_digest=ready.base_digest,
        patch_digest=ready.patch_digest,
        verification_digest="sha256:"
        + hashlib.sha256(off_trace_verification.content.encode("utf-8")).hexdigest(),
        review_digest=ready.review_digest,
        approval_digest=ready.approval_digest,
        artifact_set_digest=artifact_set_digest,
    )
    off_trace_workspace = tmp_path.parent / f"{tmp_path.name}-off-trace-workspace"
    off_trace_workspace.mkdir()
    off_trace_journal = RunJournal(
        off_trace_workspace,
        data_dir=tmp_path.parent / f"{tmp_path.name}-off-trace-data",
    )
    off_trace_run = off_trace_journal.create_run(
        title="off trace",
        source="headless",
    )
    off_trace_journal.transition(off_trace_run.id, "running")
    assert (
        off_trace_journal.record_scheduler_commit_ready(
            off_trace_run.id,
            off_trace_ready,
        )
        is True
    )
    with pytest.raises(RunJournalValidationError, match="audit"):
        off_trace_journal.record_scheduler_audit(
            off_trace_run.id,
            extended_audit,
        )


@pytest.mark.parametrize(
    (
        "projection_port",
        "expected_audit_calls",
        "result_projection",
        "failure_kind",
        "expected_ok",
    ),
    [
        ("project_scheduler_trace", 0, "available", "runtime", False),
        ("render_scheduler_trace", 1, "available", "runtime", False),
        ("render_subagent_result", 1, "failed", "runtime", False),
        ("project_scheduler_trace", 1, "available", "interrupt_once", True),
    ],
)
def test_trace_projection_failure_after_commit_returns_truthful_must_deliver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    projection_port: str,
    expected_audit_calls: int,
    result_projection: str,
    failure_kind: str,
    expected_ok: bool,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    audit_calls = 0

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, _audit):
            nonlocal audit_calls
            audit_calls += 1
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    original_projection = getattr(workflow_finalization_module, projection_port)
    projection_calls = 0

    def projection_failed(*args, **kwargs):
        nonlocal projection_calls
        projection_calls += 1
        if failure_kind == "interrupt_once" and projection_calls == 1:
            raise KeyboardInterrupt("injected one-shot projection interrupt")
        if failure_kind == "runtime":
            raise RuntimeError("injected trace projection failure")
        return original_projection(*args, **kwargs)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        workflow_finalization_module,
        projection_port,
        projection_failed,
    )
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is expected_ok
    assert result._must_deliver is True
    assert target.read_text(encoding="utf-8") == "after"
    assert audit_calls == expected_audit_calls
    assert len(result.output) <= workflow_finalization_module._WORKFLOW_OUTPUT_MAX_CHARS
    assert '"changesCommitted": true' in result.output
    if expected_ok:
        assert '"auditStatus": "persisted"' in result.output
        assert '"traceProjection": "full"' in result.output
    else:
        assert '"finalizationStatus": "projection_failed"' in result.output
        assert '"traceProjection": "failed"' in result.output
    assert (extract_subagent_result(result.output) is not None) == (
        result_projection == "available"
    )


def test_cleanup_failure_after_commit_cannot_replace_finalized_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, _audit):
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    original_close = WorkflowWorkspaceTransaction.close

    def close_then_fail(self):
        original_close(self)
        if self.parent_promotion_completed:
            raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        WorkflowWorkspaceTransaction,
        "close",
        close_then_fail,
    )
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is True
    assert result._must_deliver is True
    assert target.read_text(encoding="utf-8") == "after"
    assert extract_scheduler_trace(result.output) is not None
    assert extract_subagent_result(result.output) is not None
    assert '"changesCommitted": true' in result.output


@pytest.mark.parametrize("error_kind", ["runtime", "keyboard_interrupt"])
def test_exception_after_git_apply_cannot_be_reported_as_uncommitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_kind: str,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    original_run_checked = WorkflowWorkspaceTransaction._run_checked

    def apply_then_raise(self, args, **kwargs):
        completed = original_run_checked(self, args, **kwargs)
        if (
            "apply" in args
            and "--check" not in args
            and kwargs.get("use_operation_guard") is False
        ):
            if error_kind == "runtime":
                raise RuntimeError("post-apply cleanup failed")
            raise KeyboardInterrupt("post-apply interrupt")
        return completed

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        WorkflowWorkspaceTransaction,
        "_run_checked",
        apply_then_raise,
    )
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert target.read_text(encoding="utf-8") == "after"
    assert result._must_deliver is True
    assert len(audits) == 1
    assert audits[0].trace["committed"] is True
    assert extract_scheduler_trace(result.output)["committed"] is True
    assert '"changesCommitted": true' in result.output


def test_unknown_parent_promotion_is_projected_as_must_deliver_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    def promote_with_unknown_parent_state(self, **_kwargs):
        self._owner._parent_promotion_completed = False
        self._owner._parent_promotion_unknown = True
        raise KeyboardInterrupt("injected unresolved parent promotion")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        FrozenWorkflowChange,
        "promote",
        promote_with_unknown_parent_state,
    )
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(),
        monkeypatch,
        commit_audit_waived=True,
    )

    assert result.ok is False
    assert result._must_deliver is True
    assert "error[workflow_commit_state_unknown]" in result.output
    finalization = json.loads(
        result.output.split("=== WORKFLOW FINALIZATION v1 ===\n", 1)[1]
    )
    assert finalization["changesCommitted"] is None
    assert finalization["commitState"] == "unknown"
    assert target.read_text(encoding="utf-8") == "before"


def test_cancellation_after_commit_preserves_truthful_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    token = TurnCancellationToken("turn_" + "b" * 32)

    class CancellingAuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, _audit):
            token.request()
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=CancellingAuditSink(),
        cancellation_token=token,
        commit_audit_waived=True,
    )

    assert target.read_text(encoding="utf-8") == "after"
    assert result.ok is False
    assert result._must_deliver is True
    assert '"changesCommitted": true' in result.output
    assert '"finalizationStatus": "cancelled_after_commit"' in result.output
    assert '"auditStatus": "persisted"' in result.output


def test_cancellation_immediately_after_promotion_still_persists_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    token = TurnCancellationToken("turn_" + "c" * 32)
    audits = []

    class AuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    original_promote = FrozenWorkflowChange.promote

    def promote_then_cancel(self, **kwargs):
        original_promote(self, **kwargs)
        token.request()

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        FrozenWorkflowChange,
        "promote",
        promote_then_cancel,
    )
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=AuditSink(),
        cancellation_token=token,
        commit_audit_waived=True,
    )

    assert target.read_text(encoding="utf-8") == "after"
    assert len(audits) == 1
    assert result.ok is False
    assert result._must_deliver is True
    assert '"finalizationStatus": "cancelled_after_commit"' in result.output
    assert '"auditStatus": "persisted"' in result.output


@pytest.mark.parametrize("strict_result", ["missing", "malformed"])
def test_missing_or_malformed_analyst_result_blocks_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict_result: str,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        assert role == "analyst"
        if strict_result == "malformed":
            context._subagent_mailbox.write(
                input_data["_result_key"],
                json.dumps({"resultVersion": 1}),
                author="analyst",
            )
        return ToolResult(ok=True, output=f"analyst result is {strict_result}")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
    )

    assert result.ok is False
    assert roles == ["analyst", "analyst"]
    assert target.read_text(encoding="utf-8") == "before"


@pytest.mark.parametrize("strict_result", ["missing", "patch_mismatch"])
def test_missing_or_patch_mismatched_reviewer_result_blocks_commit_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict_result: str,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    roles: list[str] = []

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "unapproved",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        elif strict_result == "patch_mismatch":
            _write_reviewer_result(
                input_data,
                context,
                patch_digest="sha256:" + "f" * 64,
            )
        return ToolResult(ok=True, output=f"{role} completed")

    def verifier(self, **request):
        roles.append("verifier")
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
    )

    assert result.ok is False
    expected_roles = ["analyst", "executor", "verifier", "reviewer"]
    if strict_result == "missing":
        expected_roles.append("reviewer")
    assert roles == expected_roles
    assert target.read_text(encoding="utf-8") == "before"
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["status"] == "failed"
    assert trace["committed"] is False
    assert trace["transitions"][-1]["stage"] == "reviewer"
    if strict_result == "patch_mismatch":
        assert trace["terminalReason"] == "review_patch_digest_mismatch"
        assert '"verdict": "approved"' not in result.output


def test_truncated_frozen_diff_cannot_be_presented_as_approved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "x" * MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES,
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} completed")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "before"
    assert "review_evidence_truncated" in result.output
    assert '"verdict": "approved"' not in result.output
    assert len(audits) == 1
    audit = audits[0]
    replayed = project_scheduler_audit(
        profile=audit.profile,
        plan=audit.plan,
        trace=audit.trace,
        artifacts=audit.artifacts,
        contract_mode="v2",
    )
    assert replayed.trace["terminalReason"] == "review_evidence_truncated"


def test_changes_required_review_is_rejected_and_durably_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "unapproved",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            assignment = _prompt_json(input_data["prompt"], "ASSIGNMENT")
            evidence_refs = list(assignment["inputRefs"])
            context._subagent_mailbox.write(
                input_data["_result_key"],
                json.dumps(
                    {
                        "resultVersion": 1,
                        "assignmentId": assignment["assignmentId"],
                        "patchDigest": _review_patch_digest(input_data["prompt"]),
                        "verdict": "changes_required",
                        "criteria": [
                            {
                                "criterionId": "ac1",
                                "status": "failed",
                                "evidenceRefs": evidence_refs,
                            }
                        ],
                        "blockingFindings": [
                            {
                                "id": "finding_rework",
                                "severity": "high",
                                "statement": "The reviewed change needs rework.",
                                "evidenceRefs": evidence_refs,
                            }
                        ],
                        "warnings": [],
                        "requestedChecks": [],
                        "confidence": "high",
                    }
                ),
                author="reviewer",
            )
        return ToolResult(ok=True, output=f"{role} completed")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "before"
    assert "review_not_approved" in result.output
    assert len(audits) == 1
    audit = audits[0]
    assert audit.trace["terminalReason"] == "implementation_repair_no_change"
    assert any(
        transition["stage"] == "reviewer"
        and transition["outcome"] == "rejected"
        and transition["errorCode"] == "review_not_approved"
        for transition in audit.trace["transitions"]
    )
    assert (
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=audit.artifacts,
            contract_mode="v2",
        ).trace
        == audit.trace
    )


def test_public_context_hint_cannot_bypass_the_read_only_analyst(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    worker_calls: list[dict] = []

    def worker(input_data, context):
        worker_calls.append(input_data)
        _write_analyst_result(input_data, context, prepare_change=False)
        return ToolResult(ok=True, output="bounded read-only analysis")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(
        tmp_path,
        _request(kind="read_only", risk="low", complex=False),
        monkeypatch,
    )

    assert result.ok is True
    assert [item["agent_type"] for item in worker_calls] == ["plan"]
    assert "Phases: Analyst" in result.output
    assert "Phases: Parent" not in result.output


def test_worker_port_failure_revokes_the_unwritten_result_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    captured = []

    def failing_worker(input_data, context):
        captured.append((context._subagent_mailbox, input_data["_result_key"]))
        raise RuntimeError("worker port failed")

    monkeypatch.setattr(task_module, "_run", failing_worker)

    result = _run(
        tmp_path,
        _request(kind="read_only", risk="low", complex=True),
        monkeypatch,
    )

    assert result.ok is False
    assert "stage_port_failed" in result.output
    channel, result_key = captured[0]
    with pytest.raises(SchedulerArtifactError, match="invalid"):
        channel.write(
            result_key,
            '{"status":"late"}',
            author="late-worker",
        )


def test_analyst_claim_with_nonexistent_evidence_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roles: list[str] = []

    def worker(input_data, context):
        roles.append(_worker_role(input_data))
        _write_analyst_result(input_data, context, prepare_change=False)
        return ToolResult(ok=True, output="UNTRUSTED SUCCESS NARRATIVE")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(tmp_path, _request(kind="read_only"), monkeypatch)

    assert result.ok is False
    assert roles == ["analyst", "analyst"]
    assert "analyst_evidence_invalid" in result.output
    assert "UNTRUSTED SUCCESS NARRATIVE" not in result.output


def test_post_scheduler_cancellation_precedes_artifact_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    token = TurnCancellationToken("turn_" + "d" * 32)
    artifact_snapshot_calls = 0

    def worker(input_data, context):
        _write_analyst_result(input_data, context, prepare_change=False)
        return ToolResult(ok=True, output="analysis complete")

    original_execute = CentralScheduler.execute

    def execute_then_cancel(self, *args, **kwargs):
        outcome = original_execute(self, *args, **kwargs)
        token.request()
        return outcome

    original_list = SchedulerArtifactStore.list_artifact_ids

    def count_artifact_snapshot(self):
        nonlocal artifact_snapshot_calls
        artifact_snapshot_calls += 1
        return original_list(self)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(CentralScheduler, "execute", execute_then_cancel)
    monkeypatch.setattr(
        SchedulerArtifactStore,
        "list_artifact_ids",
        count_artifact_snapshot,
    )

    with pytest.raises(TurnCancellationRequested):
        _run(
            tmp_path,
            _request(kind="read_only"),
            monkeypatch,
            cancellation_token=token,
        )

    assert artifact_snapshot_calls == 0


def test_every_post_scheduler_exit_seals_session_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    cancellation = TurnCancellationRequested("turn_" + "f" * 32)
    captured_session = None

    def worker(input_data, context):
        _write_analyst_result(input_data, context, prepare_change=False)
        return ToolResult(ok=True, output="analysis complete")

    def fail_terminal_handoff(self, **_kwargs):
        nonlocal captured_session
        captured_session = self
        raise cancellation

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        workflow_runner_module._WorkflowRunSession,
        "_seal_and_finalize",
        fail_terminal_handoff,
    )

    with pytest.raises(TurnCancellationRequested) as caught:
        _run(tmp_path, _request(kind="read_only"), monkeypatch)

    assert caught.value is cancellation
    assert captured_session is not None
    assert captured_session._state is None
    assert captured_session._context is None
    assert captured_session._transaction is None
    assert captured_session._run_subagent is None
    with pytest.raises(RuntimeError, match="authority is sealed"):
        captured_session._execution_state()


def test_uncommitted_artifact_snapshot_failure_is_truthful_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    audits = []

    class AuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        _write_analyst_result(input_data, context, prepare_change=False)
        return ToolResult(ok=True, output="analysis complete")

    def fail_artifact_snapshot(_self):
        raise RuntimeError("injected artifact snapshot failure")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        SchedulerArtifactStore,
        "list_artifact_ids",
        fail_artifact_snapshot,
    )

    result = _run(
        tmp_path,
        _request(kind="read_only"),
        monkeypatch,
        event_sink=AuditSink(),
    )

    assert result.ok is False
    assert result._must_deliver is False
    assert audits == []
    assert '"changesCommitted": false' in result.output
    assert '"auditStatus": "projection_or_persistence_failed"' in result.output


def test_committed_artifact_snapshot_failure_retains_must_deliver_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    promoted = False
    audits = []

    class AuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    original_promote = FrozenWorkflowChange.promote

    def promote_then_mark(self, **kwargs):
        nonlocal promoted
        original_promote(self, **kwargs)
        promoted = True

    original_list = SchedulerArtifactStore.list_artifact_ids

    def fail_after_promotion(self):
        if promoted:
            raise RuntimeError("injected post-commit artifact snapshot failure")
        return original_list(self)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(FrozenWorkflowChange, "promote", promote_then_mark)
    monkeypatch.setattr(
        SchedulerArtifactStore,
        "list_artifact_ids",
        fail_after_promotion,
    )
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=AuditSink(),
        central_enforced=True,
    )

    assert target.read_text(encoding="utf-8") == "after"
    assert result.ok is False
    assert result._must_deliver is True
    assert audits == []
    assert '"changesCommitted": true' in result.output
    assert '"auditStatus": "projection_or_persistence_failed"' in result.output


def test_committed_snapshot_capture_failure_still_persists_final_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    audits = []

    class DurableSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    def fail_rich_capture(_cls, **_kwargs):
        raise RuntimeError("injected rich snapshot failure")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        workflow_finalization_module._WorkflowFinalizationSnapshot,
        "capture",
        classmethod(fail_rich_capture),
    )
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    result = _run(
        tmp_path,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=DurableSink(),
        central_enforced=True,
    )

    assert target.read_text(encoding="utf-8") == "after"
    assert result.ok is False
    assert result._must_deliver is True
    assert len(audits) == 1
    assert audits[0].trace["committed"] is True
    assert '"changesCommitted": true' in result.output
    assert '"finalizationStatus": "projection_failed"' in result.output
    assert '"auditStatus": "persisted"' in result.output


def test_hung_final_audit_is_bounded_and_observation_still_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    release_audit = threading.Event()
    observation_calls = 0

    class HangingAuditSink:
        def emit(self, *_args, **_kwargs):
            nonlocal observation_calls
            observation_calls += 1

        def record_scheduler_audit(self, _audit):
            release_audit.wait(1.0)
            return True

    def worker(input_data, context):
        _write_analyst_result(input_data, context, prepare_change=False)
        return ToolResult(ok=True, output="analysis complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        workflow_runner_module,
        "_FINALIZATION_AUDIT_PORT_RUNNER",
        BoundedConflictPortRunner(
            max_in_flight=1,
            default_timeout_seconds=0.02,
            cancellation_poll_seconds=0.001,
        ),
    )

    started = time.monotonic()
    result = _run(
        tmp_path,
        _request(kind="read_only"),
        monkeypatch,
        event_sink=HangingAuditSink(),
    )
    elapsed = time.monotonic() - started
    release_audit.set()

    assert elapsed < 0.5
    assert result.ok is False
    assert '"auditStatus": "projection_or_persistence_failed"' in result.output
    assert observation_calls >= 1


def test_committed_hung_final_audit_is_bounded_and_runner_capacity_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    first_target = first_workspace / "allowed.txt"
    second_target = second_workspace / "allowed.txt"
    first_target.write_text("before", encoding="utf-8")
    second_target.write_text("before", encoding="utf-8")
    audit_started = threading.Event()
    audit_finished = threading.Event()
    release_audit = threading.Event()
    audit_threads: list[threading.Thread] = []
    recovered_audits = []
    first_results: list[ToolResult] = []
    first_errors: list[BaseException] = []
    first_finished = threading.Event()

    class HangingAuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, _audit):
            audit_threads.append(threading.current_thread())
            audit_started.set()
            try:
                release_audit.wait(1.0)
                return True
            finally:
                audit_finished.set()

    class RecoveredAuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, audit):
            recovered_audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    runner = BoundedConflictPortRunner(
        max_in_flight=1,
        default_timeout_seconds=0.02,
        cancellation_poll_seconds=0.001,
    )
    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        workflow_runner_module,
        "_FINALIZATION_AUDIT_PORT_RUNNER",
        runner,
    )
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )

    def run_first_workflow() -> None:
        try:
            first_results.append(
                _run(
                    first_workspace,
                    _request(risk="medium", complex=True),
                    monkeypatch,
                    event_sink=HangingAuditSink(),
                    central_enforced=True,
                )
            )
        except BaseException as error:
            first_errors.append(error)
        finally:
            first_finished.set()

    first_thread = threading.Thread(target=run_first_workflow, daemon=True)
    first_thread.start()
    try:
        assert audit_started.wait(5.0)
        started = time.monotonic()
        completed_within_budget = first_finished.wait(1.0)
        elapsed = time.monotonic() - started
        assert completed_within_budget, (
            f"workflow remained blocked on final audit for {elapsed:.3f}s"
        )
    finally:
        release_audit.set()
        for audit_thread in tuple(audit_threads):
            audit_thread.join(2.0)
        first_thread.join(2.0)

    assert audit_finished.is_set()
    assert audit_threads and all(not item.is_alive() for item in audit_threads)
    assert not first_thread.is_alive()
    if first_errors:
        raise first_errors[0]
    assert len(first_results) == 1
    first_result = first_results[0]

    assert elapsed < 1.0
    assert first_target.read_text(encoding="utf-8") == "after"
    assert first_result.ok is False
    assert first_result._must_deliver is True
    assert '"changesCommitted": true' in first_result.output
    assert (
        '"auditStatus": "projection_or_persistence_failed"'
        in first_result.output
    )

    second_result = _run(
        second_workspace,
        _request(risk="medium", complex=True),
        monkeypatch,
        event_sink=RecoveredAuditSink(),
        central_enforced=True,
    )

    assert second_target.read_text(encoding="utf-8") == "after"
    assert second_result.ok is True
    assert second_result._must_deliver is True
    assert len(recovered_audits) == 1
    assert recovered_audits[0].trace["committed"] is True
    assert '"changesCommitted": true' in second_result.output
    assert '"auditStatus": "persisted"' in second_result.output


def test_uncommitted_audit_wait_propagates_live_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    token = TurnCancellationToken("turn_" + "e" * 32)
    audit_started = threading.Event()
    release_audit = threading.Event()

    class HangingAuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, _audit):
            audit_started.set()
            release_audit.wait(1.0)
            return True

    def worker(input_data, context):
        _write_analyst_result(input_data, context, prepare_change=False)
        return ToolResult(ok=True, output="analysis complete")

    def cancel_during_audit() -> None:
        if audit_started.wait(0.5):
            token.request()

    cancellation_thread = threading.Thread(
        target=cancel_during_audit,
        daemon=True,
    )
    monkeypatch.setattr(task_module, "_run", worker)
    cancellation_thread.start()

    started = time.monotonic()
    with pytest.raises(TurnCancellationRequested) as caught:
        _run(
            tmp_path,
            _request(kind="read_only"),
            monkeypatch,
            event_sink=HangingAuditSink(),
            cancellation_token=token,
        )
    elapsed = time.monotonic() - started
    release_audit.set()
    cancellation_thread.join(timeout=0.5)

    assert caught.value.turn_id == token.turn_id
    assert audit_started.is_set()
    assert elapsed < 0.5


def test_hung_final_observation_is_bounded_and_does_not_erase_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    release_observation = threading.Event()
    audits = []

    class HangingObservationSink:
        def emit(self, *_args, **_kwargs):
            release_observation.wait(1.0)

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        _write_analyst_result(input_data, context, prepare_change=False)
        return ToolResult(ok=True, output="analysis complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        workflow_runner_module,
        "_FINALIZATION_OBSERVATION_PORT_RUNNER",
        BoundedConflictPortRunner(
            max_in_flight=1,
            default_timeout_seconds=0.02,
            cancellation_poll_seconds=0.001,
        ),
    )

    started = time.monotonic()
    result = _run(
        tmp_path,
        _request(kind="read_only"),
        monkeypatch,
        event_sink=HangingObservationSink(),
    )
    elapsed = time.monotonic() - started
    release_observation.set()

    assert elapsed < 0.5
    assert result.ok is True
    assert len(audits) == 1
    assert '"auditStatus": "persisted"' in result.output


def test_analyst_retry_prompt_carries_bounded_validation_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    roles: list[str] = []
    analyst_instructions: list[str] = []

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            analyst_instructions.append(input_data["_result_instruction"])
            if len(analyst_instructions) == 1:
                invalid = _analyst_result_document(input_data, prepare_change=True)
                invalid["assignmentId"] = "asg_wrong_assignment"
                context._subagent_mailbox.write(
                    input_data["_result_key"],
                    json.dumps(invalid),
                    author="analyst",
                )
            else:
                _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} completed")

    def verifier(self, **request):
        roles.append("verifier")
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(
        tmp_path,
        _request(),
        monkeypatch,
        commit_audit_waived=True,
    )

    assert result.ok is True
    assert roles == ["analyst", "analyst", "executor", "verifier", "reviewer"]
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "after"
    assert len(analyst_instructions) == 2
    assert "failed validation" not in analyst_instructions[0]
    assert "failed validation" in analyst_instructions[1]
    assert "AnalystReport assignmentId does not match" in analyst_instructions[1]


def test_analyst_unrecoverable_payload_stays_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    roles: list[str] = []
    analyst_instructions: list[str] = []

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            analyst_instructions.append(input_data["_result_instruction"])
            try:
                context._subagent_mailbox.write(
                    input_data["_result_key"],
                    "I summarized the repository but wrote no JSON.",
                    author="analyst",
                )
            except Exception:
                # The real subagent_note_write tool surfaces this as a tool
                # error; a weak model may still end its turn without a
                # successful write.
                pass
            return ToolResult(ok=True, output="analyst completed")
        raise AssertionError(f"unexpected stage after analyst failure: {role}")

    monkeypatch.setattr(task_module, "_run", worker)

    result = _run(
        tmp_path,
        _request(),
        monkeypatch,
        commit_audit_waived=True,
    )

    assert result.ok is False
    assert roles == ["analyst", "analyst"]
    assert "analyst_result_invalid" in result.output
    assert len(analyst_instructions) == 2
    assert "failed validation" in analyst_instructions[1]
    assert "role result is missing" in analyst_instructions[1]
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "before"


def test_analyst_format_repair_is_audited_with_repair_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    roles: list[str] = []
    audits = []

    class AuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            document = _analyst_result_document(input_data, prepare_change=True)
            context._subagent_mailbox.write(
                input_data["_result_key"],
                "Here is the validated report:\n```json\n"
                + json.dumps(document, indent=2)
                + "\n```",
                author="analyst",
            )
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} completed")

    def verifier(self, **request):
        roles.append("verifier")
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(
        tmp_path,
        _request(),
        monkeypatch,
        event_sink=AuditSink(),
        commit_audit_waived=True,
    )

    assert result.ok is True
    assert roles == ["analyst", "executor", "verifier", "reviewer"]
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "after"
    assert len(audits) == 1
    repair_receipts = [
        json.loads(artifact.content)
        for artifact in audits[0].artifacts
        if json.loads(artifact.content).get("resultType")
        == "RoleResultRepairReceipt.v1"
    ]
    assert len(repair_receipts) == 1
    receipt = repair_receipts[0]
    assert receipt["repaired"] is True
    assert receipt["role"] == "analyst"
    assert receipt["stage"] == "analyst"
    assert receipt["receiptVersion" if "receiptVersion" in receipt else "repairReceiptVersion"] == 1


def test_executor_instruction_states_blocked_status_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    executor_instructions: list[str] = []

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            executor_instructions.append(input_data["_result_instruction"])
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} completed")

    def verifier(self, **request):
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(tmp_path, _request(), monkeypatch, commit_audit_waived=True)

    assert result.ok is True
    assert len(executor_instructions) == 1
    instruction = executor_instructions[0]
    assert "Status rule" in instruction
    assert '"blocked"' in instruction
    assert 'never report "completed" with blockers' in instruction


def test_analyst_evidence_feedback_includes_actual_line_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "allowed.txt").write_text("before\n", encoding="utf-8")
    analyst_instructions: list[str] = []

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            analyst_instructions.append(input_data["_result_instruction"])
            document = _analyst_result_document(input_data, prepare_change=True)
            if len(analyst_instructions) == 1:
                # One line in the snapshot file; the citation overshoots it.
                document["findings"][0]["evidence"][0]["lineEnd"] = 5
            context._subagent_mailbox.write(
                input_data["_result_key"],
                json.dumps(document),
                author="analyst",
            )
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text("after", encoding="utf-8")
            _write_executor_declaration(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} completed")

    def verifier(self, **request):
        return _verification_result(request)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", verifier)

    result = _run(tmp_path, _request(), monkeypatch, commit_audit_waived=True)

    assert result.ok is True
    assert len(analyst_instructions) == 2
    retry_instruction = analyst_instructions[1]
    assert "failed validation" in retry_instruction
    assert "allowed.txt has 1 lines" in retry_instruction
    assert "lineEnd 5" in retry_instruction
