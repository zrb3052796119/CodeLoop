"""Implementation repair must change actual bytes and earn fresh gate evidence."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.scheduler_audit import project_scheduler_audit
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolResult
import minicode.tools.task as task_module
import minicode.workflow_runner as runner_module
from minicode.workflow_workspace import WorkflowIsolationError, WorkflowWorkspaceTransaction

from test_workflow_v2_structured_routing import (
    _prompt_json,
    _request,
    _review_patch_digest,
    _run,
    _worker_role,
    _write_adjudication_result,
    _write_analyst_result,
    _write_executor_declaration,
    _write_reviewer_result,
)


def _repair_run(
    tmp_path, monkeypatch, *, rejection, always_bad=False,
    escape_narrow_order=False, replay_verification=False, unchanged_repair=False,
    prompt="Analyze first. Apply the structured request and review the resulting change.",
    executor_observations=None, analysis_verdict=None,
):
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    expected = "value == 'after'" if rejection == "verifier" else "value != 'before'"
    (tmp_path / "test_acceptance.py").write_text(
        "from pathlib import Path\n"
        "def test_accepted():\n"
        "    value = Path('allowed.txt').read_text()\n"
        f"    assert {expected}\n",
        encoding="utf-8",
    )
    roles = []
    executor_inputs = []
    audits = []

    class Sink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

        def record_scheduler_commit_ready(self, _ready):
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(input_data, context, prepare_change=True, risks=(
                [{"severity": "high", "statement": "The failed candidate needs risk reclassification.",
                  "evidenceRefs": ["claim_route"]}] if analysis_verdict is not None else None
            ))
        elif role == "adjudicator":
            _write_adjudication_result(input_data, context, decision=analysis_verdict)
        elif role == "executor":
            if executor_observations is not None:
                executor_observations.append((input_data["_executor_session"], context.cwd,
                                              Path(context.cwd, "allowed.txt").read_text()))
            assignment = _prompt_json(input_data["prompt"], "EXECUTOR ASSIGNMENT")
            executor_inputs.append(assignment)
            content = (
                "almost" if len(executor_inputs) == 1 or unchanged_repair
                else "still-wrong" if always_bad else "after"
            )
            Path(context.cwd, "allowed.txt").write_text(content, encoding="utf-8")
            if escape_narrow_order and len(executor_inputs) > 1:
                Path(context.cwd, "extra.txt").write_text("out of order", encoding="utf-8")
            _write_executor_declaration(input_data, context)
        elif rejection == "reviewer" and roles.count("reviewer") == 1:
            assignment = _prompt_json(input_data["prompt"], "ASSIGNMENT")
            context._subagent_mailbox.write(
                input_data["_result_key"],
                json.dumps({
                    "resultVersion": 1,
                    "assignmentId": assignment["assignmentId"],
                    "patchDigest": _review_patch_digest(input_data["prompt"]),
                    "verdict": "changes_required",
                    "criteria": [{"criterionId": "ac1", "status": "failed",
                                  "evidenceRefs": list(assignment["inputRefs"])}],
                    "blockingFindings": [], "warnings": [],
                    "requestedChecks": [], "confidence": "high",
                }),
                author="reviewer",
            )
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output="phase complete")

    def command_runner(_argv, **kwargs):
        # The host test port launches an actual acceptance test in the exact
        # frozen verification workspace. It does not synthesize pass/fail.
        return subprocess.run([sys.executable, "-m", "pytest", "-q"], **kwargs)

    monkeypatch.setattr(task_module, "_run", worker)
    def approve_test_command(_self, command, args, _cwd):
        assert command == "pytest" and args == ["-q"]
    monkeypatch.setattr(PermissionManager, "ensure_command", approve_test_command)
    actual_verifier = DeterministicVerifier(command_runner=command_runner)
    class ReplayingVerifier:
        first = None

        def run(self, **request):
            if self.first is None:
                self.first = actual_verifier.run(**request)
            return self.first

    verifier = ReplayingVerifier() if replay_verification else actual_verifier
    monkeypatch.setattr(
        runner_module, "_deterministic_verifier_for_context",
        lambda *_args, **_kwargs: verifier,
    )
    request = _request()
    if escape_narrow_order:
        request["scope"]["writePaths"].append("extra.txt")
    result = _run(tmp_path, request, monkeypatch, event_sink=Sink(), prompt=prompt)
    return result, roles, executor_inputs, audits


def test_first_real_failure_is_diagnosed_without_replacing_executor_or_candidate(tmp_path, monkeypatch):
    observations = []
    result, roles, inputs, audits = _repair_run(
        tmp_path, monkeypatch, rejection="verifier", prompt="Apply the structured request.",
        executor_observations=observations,
    )
    assert result.ok, result.output
    assert roles == ["executor", "analyst", "executor"]
    assert observations[0][0] is observations[1][0]
    assert observations[0][1] == observations[1][1]
    assert [item[2] for item in observations] == ["before", "almost"]
    assert inputs[0]["scope"] == inputs[1]["scope"]
    assert (tmp_path / "allowed.txt").read_text() == "after"
    assert len(audits) == 1
    audit = audits[0]
    assert project_scheduler_audit(profile=audit.profile, plan=audit.plan, trace=audit.trace,
        artifacts=audit.artifacts, contract_mode="v2") == audit


@pytest.mark.parametrize("decision", ["select_b", "select_a", "insufficient"])
def test_failure_diagnostic_risk_escalation_replays_without_losing_candidate(tmp_path, monkeypatch, decision):
    result, roles, _, audits = _repair_run(
        tmp_path, monkeypatch, rejection="verifier", prompt="Apply the structured request.",
        analysis_verdict=decision,
    )
    assert result.ok is (decision == "select_b"), result.output
    assert roles[:3] == ["executor", "analyst", "adjudicator"]
    assert (tmp_path / "allowed.txt").read_text() == ("after" if result.ok else "before")
    assert len(audits) == 1
    audit = audits[0]
    assert project_scheduler_audit(profile=audit.profile, plan=audit.plan, trace=audit.trace,
        artifacts=audit.artifacts, contract_mode="v2") == audit


@pytest.mark.parametrize("rejection", ["verifier", "reviewer"])
def test_implementation_repair_rewrites_bytes_and_earns_new_gate_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rejection: str
) -> None:
    result, roles, inputs, audits = _repair_run(
        tmp_path, monkeypatch, rejection=rejection
    )
    assert result.ok, result.output
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "after"
    assert roles.count("analyst") == 1
    assert roles.count("executor") == 2
    assert roles.count("reviewer") == (2 if rejection == "reviewer" else 1)
    assert inputs[0]["assignmentId"] != inputs[1]["assignmentId"]
    assert inputs[0]["scope"] == inputs[1]["scope"]
    assert inputs[0]["acceptanceCriteria"] == inputs[1]["acceptanceCriteria"]
    trace = extract_scheduler_trace(result.output)
    assert trace is not None and trace["committed"]
    assert len(audits) == 1
    audit = audits[0]
    assert sum(event["outcome"] == "repairing" for event in audit.trace["transitions"]) == 1
    assert project_scheduler_audit(
        profile=audit.profile, plan=audit.plan, trace=audit.trace,
        artifacts=audit.artifacts, contract_mode=audit.contract_mode,
    ) == audit
    executions = [json.loads(item.content) for item in audit.artifacts
                  if json.loads(item.content).get("resultType") == "ExecutionResult.v1"]
    assert len(executions) == 2
    assert executions[0]["changeSet"]["patchDigest"] != executions[1]["changeSet"]["patchDigest"]


def test_default_repair_allowance_stops_after_one_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, roles, _inputs, audits = _repair_run(
        tmp_path, monkeypatch, rejection="verifier", always_bad=True
    )
    assert not result.ok
    assert roles.count("executor") == 2
    assert "reviewer" not in roles
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "before"
    assert len(audits) == 1
    assert sum(event["outcome"] == "repairing" for event in audits[0].trace["transitions"]) == 1


def test_reopening_a_frozen_change_revokes_its_old_promotion_authority(
    tmp_path: Path,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text("almost", encoding="utf-8")
        first = transaction.freeze(allowed_paths=("allowed.txt",))
        original_change = first.change_set_document
        first_digest = first.patch_digest
        transaction.resume_for_repair(first)
        with pytest.raises(WorkflowIsolationError, match="owner is unavailable"):
            first.promote(approved_patch_digest=first_digest)
        Path(transaction.cwd, "allowed.txt").write_text("after", encoding="utf-8")
        second = transaction.freeze(allowed_paths=("allowed.txt",))
        assert second.change_set_document["baseDigest"] == original_change["baseDigest"]
        assert second.patch_digest != first_digest
        with pytest.raises(WorkflowIsolationError, match="digest changed"):
            second.promote(approved_patch_digest=first_digest)
        second.promote(approved_patch_digest=second.patch_digest)
        assert target.read_text(encoding="utf-8") == "after"


def test_compact_parent_trace_keeps_only_current_approvals_while_audit_keeps_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _roles, _inputs, audits = _repair_run(
        tmp_path, monkeypatch, rejection="reviewer"
    )
    assert result.ok, result.output
    full_trace = audits[0].trace
    parent_trace = extract_scheduler_trace(result.output)
    assert parent_trace is not None
    assert len(result.output) <= 8000
    assert '"traceProjection": "compacted"' in result.output
    assert any(event["outcome"] == "repairing" for event in full_trace["transitions"])
    assert len(parent_trace["transitions"]) < len(full_trace["transitions"])
    for stage in ("executor", "verifier", "reviewer"):
        full_latest = [event for event in full_trace["transitions"]
                       if event["stage"] == stage and event["outcome"] == "completed"][-1]
        projected = [event for event in parent_trace["transitions"]
                     if event["stage"] == stage and event["outcome"] == "completed"]
        assert len(projected) == 1
        assert projected[0]["artifactId"] == full_latest["artifactId"]


def test_repair_cannot_expand_the_analysts_narrower_implementation_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, roles, _inputs, audits = _repair_run(
        tmp_path, monkeypatch, rejection="verifier", escape_narrow_order=True
    )
    assert not result.ok
    assert roles.count("executor") == 2
    assert "change_set_invalid" in result.output
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "before"
    assert not (tmp_path / "extra.txt").exists()
    assert len(audits) == 1


def test_previous_passing_verification_cannot_authorize_a_repaired_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, roles, _inputs, audits = _repair_run(
        tmp_path, monkeypatch, rejection="reviewer", replay_verification=True
    )
    assert not result.ok
    assert roles.count("executor") == 2
    assert roles.count("reviewer") == 1
    assert "verifier_unavailable" in result.output
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "before"
    assert len(audits) == 1


def test_unchanged_repair_is_rejected_without_reusing_previous_gate_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, roles, _inputs, audits = _repair_run(
        tmp_path, monkeypatch, rejection="reviewer", unchanged_repair=True
    )
    assert not result.ok
    assert roles.count("executor") == 2
    assert roles.count("reviewer") == 1
    assert "implementation_repair_no_change" in result.output
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "before"
    assert len(audits) == 1
    assert len([event for event in audits[0].trace["transitions"]
                if event["stage"] == "verifier" and event["outcome"] == "dispatched"]) == 1
