"""Public parent delivery and the actual Reviewer prompt use host contracts."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.subagent_result import extract_subagent_result
from minicode.tooling import ToolResult, ToolUsageCounts
from minicode.workflow_delivery import WorkflowDeliveryReceipt
from minicode.tools import task as task_module
import minicode.workflow_runner as runner_module
from test_workflow_on_demand_review import _run_request
from test_workflow_v2_structured_routing import (
    _request, _worker_role, _write_analyst_result, _write_executor_declaration,
)


def _exercise(tmp_path, monkeypatch, *, user_request, submit_prompt_example=False,
              phase_output="completed", worker_workspaces=None):
    (tmp_path / "allowed.txt").write_text("before")
    (tmp_path / "test_acceptance.py").write_text(
        "from pathlib import Path\n"
        "def test_value():\n"
        "    assert Path('allowed.txt').read_text() == 'after'\n"
    )
    roles, audits, seals, events, submissions, checks = [], [], [], [], [], []

    class Sink:
        def emit(self, event_type, *, step=None, payload=None):
            events.append((event_type, payload))

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

        def record_scheduler_commit_ready(self, ready):
            seals.append(ready)
            return True

    def worker(data, context):
        role = _worker_role(data)
        roles.append(role)
        if worker_workspaces is not None:
            worker_workspaces.append(Path(context.cwd))
        if role == "analyst":
            _write_analyst_result(data, context, prepare_change=True)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text("after")
            _write_executor_declaration(data, context)
        else:
            assert role == "reviewer" and submit_prompt_example
            instruction = data["_result_instruction"]
            marker = "Minimal valid review_submit example when evidence is missing"
            tail = instruction.split(marker, 1)[1]
            example, _ = json.JSONDecoder().raw_decode(tail[tail.index("{"):])
            assert "assignmentId" not in example and "patchDigest" not in example
            submission = data["_review_submit_tool"].run(example, context)
            assert submission.ok, submission.output
            assert submission._implementation_submitted
            submissions.append(example)
            return submission
        # These deterministic test ports made no model/tool calls. Stage count
        # must never be reinterpreted as model usage by the finalizer.
        return ToolResult(ok=True, output=phase_output)

    def command_runner(_argv, **kwargs):
        checked = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "test_acceptance.py"], **kwargs)
        checks.append(checked.returncode)
        return checked

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    verifier = DeterministicVerifier(command_runner=command_runner)
    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    request = _request(risk="low", complex=False)
    request["verificationChecks"][0]["argv"] = ["pytest", "-q", "test_acceptance.py"]
    result = _run_request(tmp_path, request, event_sink=Sink(), user_request=user_request)
    return result, roles, audits, seals, events, submissions, checks


def test_analyzed_route_delivers_with_real_verifier_audit_and_typed_parent_receipt(tmp_path, monkeypatch):
    result, roles, audits, seals, events, _, checks = _exercise(
        tmp_path, monkeypatch, user_request="Analyze first, then set the value to after.")
    assert result.ok, result.output
    assert roles == ["analyst", "executor"]
    assert checks == [0]
    assert (tmp_path / "allowed.txt").read_text() == "after"
    assert len(audits) == len(seals) == 1
    assert extract_scheduler_trace(result.output)["ruleId"] == "route.analyzed_change"
    assert isinstance(result._workflow_delivery_receipt, WorkflowDeliveryReceipt)
    assert "Phases: Analyst -> Executor -> Verifier" in result.output
    assert result._usage_counts == ToolUsageCounts()
    observations = [payload for kind, payload in events
                    if kind == "subagent.completed" and payload.get("agentType") == "workflow"]
    assert len(observations) == 1
    assert observations[0]["phaseCount"] == 2
    assert observations[0]["modelTurns"] == observations[0]["toolCalls"] == 0


def test_actual_reviewer_prompt_example_is_valid_native_input_without_approving(tmp_path, monkeypatch):
    result, roles, _, seals, _, submissions, checks = _exercise(
        tmp_path, monkeypatch, user_request="Set the value to after and review the change.",
        submit_prompt_example=True)
    assert roles == ["executor", "reviewer"]
    assert checks == [0]
    assert len(submissions) == 1
    assert submissions[0]["verdict"] == "inconclusive"
    assert not result.ok, "a legal unknown verdict is not approval"
    assert seals == []
    assert (tmp_path / "allowed.txt").read_text() == "before"


@pytest.mark.parametrize("phase_output", [
    "diagnostic line\n" * 41 + "short-phase-tail",
    "诊断 evidence λ\r\n" * 23000 + "large-phase-tail",
], ids=["above-section-preview-limit", "above-256-kib"])
def test_complete_phase_archive_survives_snapshot_cleanup_with_bounded_parent_output(
        tmp_path, monkeypatch, phase_output):
    workspaces = []
    result, roles, audits, seals, _, _, checks = _exercise(
        tmp_path, monkeypatch, user_request="Set the value to after.",
        phase_output=phase_output, worker_workspaces=workspaces)

    assert result.ok, result.output
    assert roles == ["executor"]
    assert checks == [0]
    assert len(audits) == len(seals) == 1
    assert (tmp_path / "allowed.txt").read_text() == "after"
    assert workspaces and all(path != tmp_path and not path.exists() for path in workspaces)
    assert len(result.output) <= 8000
    trace = extract_scheduler_trace(result.output)
    assert trace is not None and trace["ruleId"] == "route.verified_change"
    assert extract_subagent_result(result.output) is not None
    assert isinstance(result._workflow_delivery_receipt, WorkflowDeliveryReceipt)

    reference = next(line.removeprefix("Complete phase output: ")
                     for line in result.output.splitlines()
                     if line.startswith("Complete phase output: "))
    archive = Path(reference)
    expected = ("## Executor\n\n" + phase_output).encode("utf-8")
    digest = hashlib.sha256(expected).hexdigest()
    assert archive == tmp_path / ".workflow-briefs" / f"{digest}.md"
    assert f"SHA-256: {digest}" in result.output
    assert archive.read_bytes() == expected
