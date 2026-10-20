"""Bakeoff bounds: review output, deadline handoff, and worker context."""

from __future__ import annotations

import hashlib
import json
import sys

import pytest

from minicode.central_scheduler import (
    CentralScheduler,
    DispatchStage,
    SchedulerExecutionPolicy,
    StageResult,
    TaskKind,
    TaskProfile,
    TaskRisk,
)
from minicode.deterministic_verifier import VerificationCheck, _BoundedStreamCapture
from minicode.review_probe_execution import (
    _check_document,
    derive_review_probe_check,
    validate_review_probe_execution_binding,
)
from minicode.review_probe_artifact import (
    bound_review_test_output,
    fit_review_probe_document,
)
from minicode.reviewer_verification import ReviewerCheckCapability, ReviewerProbeLedger
from minicode.scheduler_artifacts import (
    MAX_SCHEDULER_ARTIFACT_BYTES,
    SchedulerArtifactStore,
)
from minicode.scheduler_audit import _validate_verification_stream
from minicode.scheduler_trace import project_scheduler_trace
from minicode.tooling import ToolContext
from minicode.unapproved_workflow_diff import (
    render_unapproved_candidate,
    render_unapproved_workflow_diff,
)
from minicode.worker_context_bound import (
    bound_executor_transcript,
    bound_probe_feedback,
    bound_reviewer_prompt,
)
from minicode.workflow_round_budget import estimate_round_seconds, round_fits
from test_reviewer_result_submission import _bound


PATCH = "sha256:" + "ab" * 32


def _stream(payload: bytes) -> dict:
    stream = _BoundedStreamCapture()
    stream.add(payload)
    return stream.evidence()


def _evidence(stdout: bytes, *, status: str = "failed", exit_code: int = 1) -> dict:
    argv = ["python", "-m", "pytest", "-q", "tests/test_inventory.py"]
    return {
        "evidenceVersion": 1,
        "checkId": "public",
        "kind": "tests",
        "argv": argv,
        "resolvedExecutable": sys.executable,
        "commandDigest": "sha256:" + hashlib.sha256("\0".join(argv).encode()).hexdigest(),
        "status": status,
        "exitCode": exit_code,
        "durationMs": 4,
        "failureReason": None,
        "stdout": _stream(stdout),
        "stderr": _stream(b""),
    }


def _oversized_receipt(probe_id: str) -> dict:
    stdout = b"noise line\n" * 4000
    stdout += (
        b"FAILED tests/test_inventory.py::test_qty - assert 0 == 1\n"
        b"E       AssertionError: assert 0 == 1\n"
    )
    return {
        "probeId": probe_id,
        "patchDigest": PATCH,
        "status": "failed",
        "testReport": {
            "collected": 1,
            "passed": 0,
            "failed": 1,
            "errors": 0,
            "skipped": 0,
            "reportDigest": "sha256:" + "d" * 64,
            "executionReport": {"blob": "Q" * 80_000},
        },
        "evidence": _evidence(stdout),
        "executionBinding": {"bindingVersion": 1, "note": "kept when it fits"},
    }


def test_review_probe_receipt_stays_within_the_artifact_limit_and_keeps_failures():
    document = {
        "probeVersion": 1,
        "resultType": "ReviewProbeExecution.v1",
        "workflowId": "wf_bounds",
        "taskId": "task_bounds",
        "assignmentId": "asg_bounds",
        "patchDigest": PATCH,
        "probeId": "review_probe_" + "a" * 24,
        "sourceRef": "artifact_review_probe_source_" + "b" * 32,
        "sequence": 1,
        "status": "failed",
        **{key: value for key, value in _oversized_receipt("review_probe_" + "a" * 24).items()
           if key in {"evidence", "testReport", "executionBinding"}},
    }
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(raw.encode("utf-8")) > MAX_SCHEDULER_ARTIFACT_BYTES
    fitted = fit_review_probe_document(document)
    encoded = json.dumps(fitted, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(encoded.encode("utf-8")) <= MAX_SCHEDULER_ARTIFACT_BYTES
    assert "executionReport" not in fitted["testReport"]
    preview = fitted["evidence"]["stdout"]["preview"]
    assert "tests/test_inventory.py::test_qty" in preview
    assert "assert 0 == 1" in preview
    assert _validate_verification_stream(fitted["evidence"]["stdout"])
    assert _validate_verification_stream(fitted["evidence"]["stderr"])
    store = SchedulerArtifactStore(scheduler_authority=object())
    authority = store._scheduler_authority
    store.publish("artifact_review_probe_run_" + "c" * 32, fitted, authority=authority)


def test_review_test_tool_output_summarises_a_huge_pytest_log():
    assignment, _store, channel, _tool, _kwargs = _bound()
    seen = {}

    def run(probe, _context):
        receipt = _oversized_receipt(probe.probe_id)
        base = VerificationCheck(
            "public", "tests",
            ("python", "-m", "pytest", "-q", "tests/test_inventory.py"), 30,
        )
        derived, target = derive_review_probe_check(base, "tests/test_inventory.py")
        evidence = receipt["evidence"]
        evidence["argv"] = list(derived.argv)
        evidence["commandDigest"] = (
            "sha256:" + hashlib.sha256("\0".join(derived.argv).encode()).hexdigest()
        )
        receipt["executionBinding"] = {
            "bindingVersion": 1,
            "patchDigest": receipt["patchDigest"],
            "baseCheck": _check_document(base),
            "derivedCheck": _check_document(derived),
            "originalTarget": "tests/test_inventory.py",
            "probeTarget": target,
            "originalTargetDigest": "sha256:" + "cd" * 32,
            "probeSourceDigest": probe.source_digest,
        }
        seen["raw"] = len(json.dumps(receipt).encode("utf-8"))
        return receipt

    capability = ReviewerCheckCapability(
        assignment=assignment,
        channel=channel,
        ledger=ReviewerProbeLedger(),
        patch_digest=PATCH,
        run_probe=run,
    )
    result = capability.tool.run(
        capability.tool.validator({
            "criterionIds": ["ac_behavior"],
            "scenario": "quantity starts at zero",
            "code": "def test_qty():\n    assert False\n",
        }),
        ToolContext(_subagent_mailbox=channel),
    )
    assert seen["raw"] > MAX_SCHEDULER_ARTIFACT_BYTES
    assert len(result.output.encode("utf-8")) <= MAX_SCHEDULER_ARTIFACT_BYTES
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    validate_review_probe_execution_binding(payload)
    assert payload["executionBinding"]["probeTarget"].endswith("__minicode_review_probe__.py")
    assert "tests/test_inventory.py::test_qty" in result.output
    assert "assert 0 == 1" in result.output
    assert payload["recovery"]["tool"] == "review_probe_read"
    rendered = bound_review_test_output(payload, payload["recovery"])
    assert len(rendered.encode("utf-8")) <= MAX_SCHEDULER_ARTIFACT_BYTES


def test_small_review_receipt_is_not_rewritten():
    document = _oversized_receipt("review_probe_" + "e" * 24)
    document["testReport"].pop("executionReport")
    document["evidence"]["stdout"] = _stream(b"FAILED tests/test_inventory.py::test_qty\n")
    document["evidence"]["stderr"] = _stream(b"")
    assert fit_review_probe_document(document) is document


def test_round_estimate_uses_the_slowest_finished_round_and_allows_the_first():
    assert estimate_round_seconds([]) is None
    assert estimate_round_seconds([0, -1]) is None
    assert estimate_round_seconds([12.0, 40.0, 9.0]) == 40.0
    assert round_fits(remaining_seconds=None, samples=[40.0]) is True
    assert round_fits(remaining_seconds=10.0, samples=[]) is True
    assert round_fits(remaining_seconds=40.0, samples=[40.0]) is True
    assert round_fits(remaining_seconds=39.9, samples=[40.0]) is False


def _reviewed_profile() -> TaskProfile:
    return TaskProfile(
        task_id="task_round_budget",
        kind=TaskKind.CHANGE,
        risk=TaskRisk.HIGH,
        complex=True,
        acceptance_executable=True,
    )


def _patch_result(artifact_id: str, *, repairable: bool = False, approved: bool = True) -> StageResult:
    return StageResult(
        status="completed",
        artifact_id=artifact_id,
        patch_digest=PATCH,
        approved=approved,
        repairable=repairable,
    )


def test_scheduler_does_not_start_a_repair_round_that_cannot_fit():
    now = {"t": 0.0}
    executors: list[int] = []
    reviews: list[int] = []

    def run_worker(assignment):
        if assignment.stage is DispatchStage.EXECUTOR:
            executors.append(assignment.repair_index)
            return _patch_result("artifact_execution")
        if assignment.stage is DispatchStage.REVIEWER:
            now["t"] = 800.0
            reviews.append(assignment.repair_index)
            return _patch_result("artifact_review", approved=False, repairable=True)
        return StageResult(status="completed", artifact_id="artifact_analysis")

    scheduler = CentralScheduler()
    profile = _reviewed_profile()
    transitions = []
    outcome = scheduler.execute(
        profile,
        workflow_id="wf_skip_repair",
        run_worker=run_worker,
        run_verifier=lambda _assignment: _patch_result("artifact_verification"),
        commit=lambda _request: pytest.fail("commit not reached"),
        on_transition=transitions.append,
        deadline_monotonic=900.0,
        clock=lambda: now["t"],
    )

    assert executors == [0]
    assert reviews == [0]
    assert outcome.status == "timed_out"
    assert outcome.committed is False
    assert outcome.failure_stage is DispatchStage.EXECUTOR
    assert outcome.terminal_reason == "round_budget_insufficient"
    assert outcome.patch_digest == PATCH
    trace = project_scheduler_trace(scheduler.plan(profile), outcome, transitions)
    assert trace["transitions"][-2]["outcome"] == "repairing"
    assert trace["transitions"][-1]["stage"] == "executor"
    assert trace["transitions"][-1]["outcome"] == "timed_out"
    assert trace["transitions"][-1]["errorCode"] == "round_budget_insufficient"
    assert trace["transitions"][-1]["artifactId"] is None


def test_scheduler_does_not_start_a_review_round_that_cannot_fit():
    now = {"t": 0.0}
    reviews: list[int] = []

    def run_worker(assignment):
        if assignment.stage is DispatchStage.EXECUTOR:
            if assignment.repair_index:
                now["t"] = 960.0
            return _patch_result("artifact_execution")
        if assignment.stage is DispatchStage.REVIEWER:
            reviews.append(assignment.repair_index)
            now["t"] = 100.0
            return _patch_result("artifact_review", approved=False, repairable=True)
        return StageResult(status="completed", artifact_id="artifact_analysis")

    scheduler = CentralScheduler()
    profile = _reviewed_profile()
    transitions = []
    outcome = scheduler.execute(
        profile,
        workflow_id="wf_skip_review",
        run_worker=run_worker,
        run_verifier=lambda _assignment: _patch_result("artifact_verification"),
        commit=lambda _request: pytest.fail("commit not reached"),
        on_transition=transitions.append,
        deadline_monotonic=1000.0,
        clock=lambda: now["t"],
        policy=SchedulerExecutionPolicy(implementation_max_repairs=2),
    )

    assert reviews == [0]
    assert outcome.status == "timed_out"
    assert outcome.failure_stage is DispatchStage.REVIEWER
    assert outcome.terminal_reason == "round_budget_insufficient"
    assert outcome.patch_digest == PATCH
    trace = project_scheduler_trace(scheduler.plan(profile), outcome, transitions)
    assert trace["transitions"][-1]["stage"] == "reviewer"
    assert trace["transitions"][-1]["outcome"] == "timed_out"
    assert trace["transitions"][-1]["errorCode"] == "round_budget_insufficient"
    assert trace["transitions"][-1]["artifactId"] is None


def test_unapproved_diff_is_marked_and_prefers_a_reviewed_candidate():
    diff = "diff --git a/inventory.py b/inventory.py\n+qty = 0\n"
    reviewed = render_unapproved_workflow_diff(
        diff_document={
            "diff": diff,
            "complete": True,
            "encoding": "utf-8",
            "patchDigest": PATCH,
        },
        review={"verdict": "changes_required"},
        patch_digest=PATCH,
    )
    assert reviewed.startswith("=== UNAPPROVED WORKFLOW DIFF v1 ===")
    assert '"approval": "unapproved"' in reviewed
    assert '"source": "reviewed"' in reviewed
    assert '"promoted": false' in reviewed
    assert "not approved" in reviewed
    assert "+qty = 0" in reviewed
    executor = render_unapproved_workflow_diff(
        diff_document={"diff": diff, "complete": True, "patchDigest": PATCH},
        review={"verdict": "not_applicable"},
    )
    assert '"source": "executor"' in executor
    captured = render_unapproved_candidate({
        "baseDigest": PATCH,
        "files": [{"path": "inventory.py", "diff": diff}],
    })
    assert "inventory.py" in captured
    assert '"approval": "unapproved"' in captured
    assert render_unapproved_workflow_diff(diff_document={"diff": "  "}, review=None) == ""


def test_executor_transcript_summarises_earlier_rounds_and_keeps_the_latest():
    huge = "SOURCE " + ("x" * 120_000)
    messages = [
        {"role": "user", "content": "implement inventory"},
        {"role": "assistant_tool_call", "toolName": "read_file", "input": {"path": "inventory.py"}},
        {"role": "tool_result", "toolName": "read_file", "content": huge},
        {"role": "assistant", "content": "Updated the quantity projection."},
        {"role": "user", "content": "repair the failed assertion"},
        {
            "role": "tool_result",
            "content": "FAILED tests/test_inventory.py::test_qty\nE       AssertionError: assert 0 == 1\n",
        },
    ]
    bounded = bound_executor_transcript(messages)
    rendered = "\n".join(message.get("content", "") for message in bounded if isinstance(message.get("content"), str))
    assert "x" * 1000 not in rendered
    assert "EARLIER ROUNDS SUMMARY" in rendered
    assert "inventory.py" in rendered
    assert "repair the failed assertion" in rendered
    assert "tests/test_inventory.py::test_qty" in rendered
    assert sum(len(str(message.get("content", ""))) for message in bounded) <= 48_000
    assert bound_executor_transcript([{"role": "user", "content": "only"}])[0]["content"] == "only"


def test_reviewer_prompt_keeps_the_diff_and_drops_full_transcripts():
    diff = {
        "complete": True,
        "encoding": "utf-8",
        "patchDigest": PATCH,
        "diff": "diff --git a/inventory.py b/inventory.py\n+qty = 0\n",
    }
    verification = {"status": "failed", "patchDigest": PATCH, "checks": [{"checkId": "tests", "status": "failed"}]}
    execution = {"status": "completed", "blob": "Y" * 200_000}
    feedback = {
        "probes": [{
            "probeId": "review_probe_" + "a" * 24,
            "criterionIds": ["ac1"],
            "scenario": "quantity",
            "code": "Z" * 200_000,
        }],
        "attempts": [{
            "probeId": "review_probe_" + "a" * 24,
            "status": "failed",
            "evidence": {"stdout": {"preview": "FAILED tests/test_inventory.py::test_qty\nE AssertionError: assert 0 == 1\n"}},
        }],
        "activeProbeIds": ["review_probe_" + "a" * 24],
        "retiredProbeIds": [],
    }
    prompt = (
        "Inspect the frozen change.\n\nORIGINAL TASK:\nbuild inventory\n\nEXECUTION RESULT:\n"
        + json.dumps(execution)
        + "\n\nFROZEN DIFF:\n"
        + json.dumps(diff)
        + "\n\nVERIFICATION RESULT:\n"
        + json.dumps(verification)
        + "\n\nACCEPTANCE EVIDENCE MATRIX:\n"
        + json.dumps({"criteria": [{"criterionId": "ac1", "checkIds": ["tests"]}]})
        + "\n=== REVIEW SOURCE CONTEXT ===\nFILE inventory.py relevant lines\n"
        + "\n\nINDEPENDENT REQUIREMENT CHECKS:\nUse review_test.\n"
        + json.dumps(feedback)
    )
    bounded = bound_reviewer_prompt(prompt)
    assert len(bounded) < len(prompt)
    assert len(bounded) <= 80_000
    assert "Y" * 1000 not in bounded
    assert "Z" * 1000 not in bounded
    frozen = json.loads(bounded.split("\n\nFROZEN DIFF:\n", 1)[1].split("\n\n", 1)[0])
    assert frozen["complete"] is True
    assert "+qty = 0" in frozen["diff"]
    assert "FILE inventory.py" in bounded
    assert "tests/test_inventory.py::test_qty" in bounded
    assert "assert 0 == 1" in bounded
    compact = bound_probe_feedback(json.dumps(feedback))
    assert "Z" * 100 not in compact
    assert "test_qty" in compact
    assert bound_reviewer_prompt("short review prompt") == "short review prompt"
