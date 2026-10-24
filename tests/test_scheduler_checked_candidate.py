"""Host candidates remain unassessed until fresh verification and review.

The worker stop is a fixture. File tools, isolated pytest, candidate production,
final verification, audit replay and promotion use their real production paths.
This tests audit authority, not provider performance or task completeness.
"""

from dataclasses import replace
import json
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.scheduler_audit import SchedulerAuditValidationError, project_scheduler_audit
from minicode.subagent_result import extract_subagent_result
from minicode.tooling import ToolRegistry, ToolResult, ToolTerminationReason
from minicode.tools.edit_file import edit_file_tool
import minicode.tools.task as task_module
import minicode.workflow_runner as runner_module
from test_workflow_v2_structured_routing import (
    _prompt_json, _request, _run, _worker_role, _write_analyst_result,
)


def _candidate_run(tmp_path, monkeypatch, *, approve=True, fail_first=False,
                   always_bad=False, executor_observations=None):
    target = tmp_path / "allowed.txt"
    target.write_text("before")
    (tmp_path / "test_acceptance.py").write_text(
        "from pathlib import Path\n"
        "def test_changed():\n"
        "    assert Path('allowed.txt').read_text() == 'after'\n"
    )
    audits, checks, roles = [], [], []
    candidate_errors = []
    parse_candidate = runner_module.parse_execution_candidate

    def inspect_candidate(*args, **kwargs):
        try:
            return parse_candidate(*args, **kwargs)
        except Exception as error:
            candidate_errors.append((repr(error), kwargs.get("receipt_documents")))
            raise

    monkeypatch.setattr(runner_module, "parse_execution_candidate", inspect_candidate)

    class Sink:
        def emit(self, *_args, **_kwargs):
            pass

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

        def record_scheduler_commit_ready(self, _ready):
            return True

    class ObservedVerifier(DeterministicVerifier):
        def run(self, **kwargs):
            assert target.read_text() == "before", "neither implementation nor final checks may commit"
            result = super().run(**kwargs)
            checks.extend(item["exitCode"] for item in result["checks"])
            return result

    class TestSandbox:
        # The test process is trusted. Production sandbox selection is not
        # replaced; this host port lets the actual managed verifier run pytest.
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    verifier = ObservedVerifier(
        sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable},
    )
    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True)
        elif role == "executor":
            cap = input_data["_executor_check_capability"]
            registry = cap.bind_registry(ToolRegistry([edit_file_tool, input_data["_executor_submit_tool"]]))
            revision = roles.count("executor")
            assignment = _prompt_json(input_data["prompt"], "EXECUTOR ASSIGNMENT")
            if executor_observations is not None:
                budget = context._agent_budget
                while hasattr(budget, "_budget"):
                    budget = budget._budget
                executor_observations.append({
                    "assignment": assignment, "session": input_data["_executor_session"],
                    "budget": budget, "checks_before": list(checks),
                    "prompt": input_data["prompt"],
                })
            failed = fail_first and (revision == 1 or always_bad)
            edited = registry.execute("edit_file", {
                "path": "allowed.txt", "old": "before" if revision == 1 else "almost",
                "new": "almost" if revision == 1 and failed else "still wrong" if failed else "after",
            }, context)
            assert edited.ok, edited.output
            assert cap.completion_pending is failed, edited.output
            if fail_first and revision > 1 and not failed:
                assert "IMPLEMENTATION REPAIR REQUEST" in input_data["prompt"]
                submit = input_data["_executor_submit_tool"]
                count = submit.input_schema["properties"]["steps"]["items"]["properties"]["step"]["maximum"]
                submitted = registry.execute("executor_submit", {
                    "status": "completed", "steps": [
                        {"step": number, "status": "completed"} for number in range(1, count + 1)
                    ], "deviations": [], "blockers": [],
                }, context)
                assert submitted.ok, submitted.output
                return ToolResult(ok=True, output="Repaired candidate submitted through the real completion gate.")
            return ToolResult(ok=False, output="No model declaration; the next request could not be admitted.",
                              _termination_reason=ToolTerminationReason.BUDGET_EXCEEDED)
        else:
            assert role == "reviewer" and target.read_text() == "before"
            if not fail_first:
                assert '"status": "awaiting_verification"' in input_data["prompt"]
            submit = input_data["_review_submit_tool"]
            result = ToolRegistry([submit]).execute("review_submit", {
                "verdict": "approved" if approve else "inconclusive",
                "criteria": [{"criterionId": "ac1", "status": "satisfied"}] if approve else [],
                "findings": [], "warnings": [], "requestedChecks": [], "confidence": "high",
            }, context)
            assert result.ok, result.output
        return ToolResult(ok=True, output="Fixture role ended.")

    monkeypatch.setattr(task_module, "_run", worker)
    request = _request()
    request["verificationChecks"][0]["argv"] = ["python", "-m", "pytest", "-q"]
    result = _run(tmp_path, request, monkeypatch, event_sink=Sink(),
                  prompt="Analyze first. Apply the structured request and review the resulting change.")
    assert not candidate_errors, json.dumps(candidate_errors, indent=2)
    assert len(audits) == 1, result.output
    return result, audits[0], target, checks, roles


def _replay(audit, *, artifacts=None, plan=None, trace=None):
    return project_scheduler_audit(
        profile=audit.profile, plan=audit.plan if plan is None else plan,
        trace=audit.trace if trace is None else trace,
        artifacts=audit.artifacts if artifacts is None else artifacts, contract_mode="v2",
    )


def _documents(audit):
    return {artifact.artifact_id: json.loads(artifact.content) for artifact in audit.artifacts}


def test_checked_host_candidate_earns_fresh_gates_before_commit(tmp_path, monkeypatch):
    result, audit, target, checks, roles = _candidate_run(tmp_path, monkeypatch)
    assert result.ok, result.output
    assert checks == [0, 0], "implementation pass cannot substitute for a fresh final Verifier"
    assert roles == ["analyst", "executor", "reviewer"]
    assert target.read_text() == "after"
    assert audit.trace["committed"] and _replay(audit) == audit
    documents = _documents(audit)
    candidate = next(value for value in documents.values() if value.get("resultType") == "ExecutionCandidate.v1")
    assert candidate["status"] == "awaiting_verification"
    assert candidate["unassessedStepIds"] and "stepResults" not in candidate
    assert not any(value.get("resultType") == "ExecutionResult.v1" for value in documents.values())
    assert extract_subagent_result(result.output)["outcome"] == "completed"


def test_unknown_review_never_promotes_a_checked_host_candidate(tmp_path, monkeypatch):
    result, audit, target, checks, _roles = _candidate_run(tmp_path, monkeypatch, approve=False)
    assert not result.ok and not audit.trace["committed"]
    assert target.read_text() == "before" and checks == [0, 0]
    assert _replay(audit) == audit
    assert extract_subagent_result(result.output)["outcome"] != "completed"


def test_failed_candidate_needs_fresh_verifier_failure_before_same_owner_repair(tmp_path, monkeypatch):
    observations = []
    result, audit, target, checks, roles = _candidate_run(
        tmp_path, monkeypatch, fail_first=True, executor_observations=observations,
    )
    assert result.ok, result.output
    assert checks == [1, 1, 0, 0], "both candidate revisions need fresh final verification"
    assert roles == ["analyst", "executor", "executor", "reviewer"]
    assert target.read_text() == "after" and audit.trace["committed"]
    assert observations[1]["checks_before"] == [1, 1], "automatic failure alone cannot dispatch repair"
    assert observations[0]["session"] is observations[1]["session"]
    assert observations[0]["budget"] is observations[1]["budget"]
    first, repaired = (item["assignment"] for item in observations)
    assert first["assignmentId"] != repaired["assignmentId"]
    assert first["scope"] == repaired["scope"]
    assert first["acceptanceCriteria"] == repaired["acceptanceCriteria"]
    assert _replay(audit) == audit
    documents = _documents(audit)
    candidate = next(value for value in documents.values() if value.get("resultType") == "ExecutionCandidate.v1")
    assert candidate["status"] == "awaiting_verification" and "stepResults" not in candidate
    receipt = documents[candidate["implementationReceiptRefs"][0]]["receipt"]
    assert receipt["status"] == "failed" and receipt["evidence"]["exitCode"] == 1
    verifications = [value for value in documents.values() if value.get("resultType") == "VerificationResult.v1"]
    assert {value["status"] for value in verifications} == {"failed", "passed"}
    failed_verification = next(value for value in verifications if value["status"] == "failed")
    assert failed_verification["patchDigest"] == candidate["changeSet"]["patchDigest"]
    assert sum(item["outcome"] == "repairing" for item in audit.trace["transitions"]) == 1
    assert extract_subagent_result(result.output)["outcome"] == "completed"


def test_failed_candidate_does_not_bypass_existing_repair_allowance(tmp_path, monkeypatch):
    result, audit, target, checks, roles = _candidate_run(
        tmp_path, monkeypatch, fail_first=True, always_bad=True,
    )
    assert not result.ok and not audit.trace["committed"]
    assert checks == [1, 1, 1, 1] and roles == ["analyst", "executor", "executor"]
    assert target.read_text() == "before" and _replay(audit) == audit
    assert sum(item["outcome"] == "repairing" for item in audit.trace["transitions"]) == 1
    assert extract_subagent_result(result.output)["outcome"] != "completed"


@pytest.mark.parametrize("forgery", [
    "missing_receipt", "stale_receipt", "different_command", "synthetic_pass",
    "passed_nonzero", "failed_zero", "inconclusive_receipt",
    "assessed_steps", "missing_criterion", "worker_result_key", "missing_frozen_diff",
])
def test_candidate_audit_rejects_unearned_or_rebound_authority(tmp_path, monkeypatch, forgery):
    result, audit, _target, _checks, _roles = _candidate_run(tmp_path, monkeypatch)
    assert result.ok, result.output
    documents = _documents(audit)
    candidate_id = next(key for key, value in documents.items() if value.get("resultType") == "ExecutionCandidate.v1")
    candidate = documents[candidate_id]
    receipt_id = candidate["implementationReceiptRefs"][0]
    target_id = receipt_id
    if forgery == "missing_receipt":
        artifacts = tuple(item for item in audit.artifacts if item.artifact_id != receipt_id)
    elif forgery == "missing_frozen_diff":
        artifacts = tuple(item for item in audit.artifacts
                          if documents[item.artifact_id].get("diffVersion") != 1)
    else:
        if forgery == "stale_receipt":
            documents[receipt_id]["receipt"]["patchDigest"] = "sha256:" + "f" * 64
        elif forgery == "different_command":
            documents[receipt_id]["receipt"]["argv"] = ["true"]
        elif forgery == "synthetic_pass":
            documents[receipt_id]["receipt"]["evidence"] = {"status": "passed", "exitCode": 0}
        elif forgery == "passed_nonzero":
            documents[receipt_id]["receipt"]["evidence"]["exitCode"] = 4
        elif forgery in {"failed_zero", "inconclusive_receipt"}:
            status = "failed" if forgery == "failed_zero" else "inconclusive"
            documents[receipt_id]["receipt"]["status"] = status
            documents[receipt_id]["receipt"]["evidence"]["status"] = status
        elif forgery == "assessed_steps":
            target_id = candidate_id
            candidate["stepResults"] = [{"stepId": candidate["unassessedStepIds"][0], "status": "completed"}]
        elif forgery == "missing_criterion":
            target_id = candidate["orderId"]
            documents[target_id]["criterionIds"] = []
        elif forgery == "worker_result_key":
            # A candidate written to a Worker result slot is not the host's
            # canonical execution artifact even if all content is copied.
            target_id = candidate_id
        artifacts = tuple(
            replace(item, content=json.dumps(documents[target_id], sort_keys=True, separators=(",", ":")))
            if item.artifact_id == target_id else item for item in audit.artifacts
        )
    trace = audit.trace
    if forgery == "worker_result_key":
        from copy import deepcopy

        trace = deepcopy(audit.trace)
        forged_id = "artifact_worker_owned_result"
        artifacts = tuple(replace(item, artifact_id=forged_id) if item.artifact_id == candidate_id else item
                          for item in artifacts)
        for item in trace["transitions"]:
            if item["artifactId"] == candidate_id:
                item["artifactId"] = forged_id
    with pytest.raises(SchedulerAuditValidationError):
        _replay(audit, artifacts=artifacts, trace=trace)


def test_candidate_cannot_enter_historical_policy_without_mandatory_coverage_review(tmp_path, monkeypatch):
    result, audit, *_ = _candidate_run(tmp_path, monkeypatch)
    assert result.ok, result.output
    # This medium-risk route has the same stage order in policy 3 and 4, so
    # rejection is the candidate policy guard, not a malformed plan fixture.
    plan = replace(audit.plan, policy_version=3)
    trace = {**audit.trace, "policyVersion": 3}
    with pytest.raises(SchedulerAuditValidationError, match="mandatory Reviewer authority"):
        _replay(audit, plan=plan, trace=trace)


def test_passing_candidate_and_verifier_cannot_erase_required_reviewer(tmp_path, monkeypatch):
    result, audit, *_ = _candidate_run(tmp_path, monkeypatch)
    assert result.ok, result.output
    from copy import deepcopy

    trace = deepcopy(audit.trace)
    trace["transitions"] = [item for item in trace["transitions"] if item["stage"] != "reviewer"]
    # Preserve a well-linked sequence: rejection must concern the missing
    # required stage, not accidentally broken event numbering or parent IDs.
    previous = None
    for index, item in enumerate(trace["transitions"], 1):
        item["sequence"] = index
        item["parentDecisionId"] = previous
        previous = item["decisionId"]
    with pytest.raises(SchedulerAuditValidationError):
        _replay(audit, trace=trace)
