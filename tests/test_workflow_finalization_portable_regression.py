"""Portable regression proof for bounded post-commit final audit delivery.

This file intentionally imports only production modules that also exist at
``f8ae6fd``.  The same bytes can therefore exercise the old synchronous audit
port instead of failing during test collection.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

import pytest

import minicode.tools.task as task_module
import minicode.workflow_runner as workflow_runner_module
from minicode.conflict_port_runner import BoundedConflictPortRunner
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.subagent_mailbox import SubagentMailbox
from minicode.tooling import ToolContext, ToolResult


def _request() -> dict[str, object]:
    return {
        "requestVersion": 1,
        "profile": {
            "taskId": "task_portable_final_audit",
            "kind": "change",
            "risk": "low",
            "complex": False,
            "parentHasSufficientContext": True,
        },
        "scope": {
            "readRoots": ["."],
            "writePaths": ["allowed.txt"],
            "forbiddenPaths": [".env", ".git"],
        },
        "criteria": [
            {
                "id": "ac1",
                "kind": "behavior",
                "text": "The target is updated.",
            }
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


def _verification_result(request: dict[str, object]) -> dict[str, object]:
    checks = request.get("checks")
    check = checks[0] if isinstance(checks, tuple) and checks else None
    check_id = str(getattr(check, "check_id", "check_tests"))
    check_kind = str(getattr(check, "kind", "tests"))
    argv = list(getattr(check, "argv", ("pytest", "-q")))
    workflow_id = str(request["workflow_id"])
    execution_assignment_id = str(request["execution_assignment_id"])
    patch_digest = str(request["patch_digest"])
    artifact_ref = "artifact_verification_" + hashlib.sha256(
        (
            f"{workflow_id}\0{execution_assignment_id}\0"
            f"{patch_digest}\0{check_id}"
        ).encode("utf-8")
    ).hexdigest()[:24]
    empty_stream = {
        "byteCount": 0,
        "digest": "sha256:" + hashlib.sha256(b"").hexdigest(),
        "preview": "",
        "previewBase64": "",
        "truncated": False,
    }
    evidence_sink = request.get("evidence_sink")
    if callable(evidence_sink):
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
        "verificationId": "verification_portable_final_audit",
        "workflowId": workflow_id,
        "taskId": str(request["task_id"]),
        "executionAssignmentId": execution_assignment_id,
        "patchDigest": patch_digest,
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


def _prompt_json(prompt: str, label: str) -> dict[str, object]:
    match = re.search(rf"{re.escape(label)}:\n([^\n]+)", prompt)
    assert match is not None
    value = json.loads(match.group(1))
    assert isinstance(value, dict)
    return value


def _worker_role(input_data: dict[str, object]) -> str:
    prompt = str(input_data["prompt"])
    for role in ("analyst", "executor", "reviewer"):
        if f'"role": "{role}"' in prompt:
            return role
    raise AssertionError("worker prompt did not carry a locked role")


def _write_analyst_result(input_data: dict[str, object], context: object) -> None:
    prompt = str(input_data["prompt"])
    assignment_match = re.search(r'"assignmentId":\s*"([^"]+)"', prompt)
    assert assignment_match is not None
    result = {
        "resultVersion": 1,
        "assignmentId": assignment_match.group(1),
        "status": "completed",
        "findings": [
            {
                "claimId": "claim_route",
                "statement": "The bounded route is supported.",
                "evidence": [
                    {"path": "allowed.txt", "lineStart": 1, "lineEnd": 1}
                ],
                "confidence": "high",
            }
        ],
        "unknowns": [],
        "risks": [],
        "implementationOrderCandidate": {
            "required": True,
            "steps": [
                {
                    "id": "s_route",
                    "action": "Apply the bounded change.",
                    "allowedPaths": ["allowed.txt"],
                }
            ],
            "verificationRequests": [],
        },
        "ballot": None,
    }
    context._subagent_mailbox.write(  # type: ignore[attr-defined]
        str(input_data["_result_key"]),
        json.dumps(result),
        author="analyst",
    )


def _write_executor_result(input_data: dict[str, object], context: object) -> None:
    prompt = str(input_data["prompt"])
    assignment = _prompt_json(prompt, "EXECUTOR ASSIGNMENT")
    order = _prompt_json(prompt, "LOCKED IMPLEMENTATION ORDER")
    steps = order["steps"]
    assert isinstance(steps, list)
    result = {
        "resultVersion": 1,
        "assignmentId": assignment["assignmentId"],
        "orderId": order["orderId"],
        "status": "completed",
        "stepResults": [
            {
                "stepId": step["id"],
                "status": "completed",
                "artifactRefs": [],
            }
            for step in steps
            if isinstance(step, dict)
        ],
        "deviations": [],
        "selfChecks": [],
        "blockers": [],
    }
    context._subagent_mailbox.write(  # type: ignore[attr-defined]
        str(input_data["_result_key"]),
        json.dumps(result),
        author="executor",
    )


def _write_reviewer_result(input_data: dict[str, object], context: object) -> None:
    prompt = str(input_data["prompt"])
    assignment = _prompt_json(prompt, "ASSIGNMENT")
    assignment_match = re.search(r'"assignmentId":\s*"([^"]+)"', prompt)
    assert assignment_match is not None
    digests = set(re.findall(r'"patchDigest": "(sha256:[0-9a-f]{64})"', prompt))
    assert len(digests) == 1
    input_refs = assignment["inputRefs"]
    assert isinstance(input_refs, list)
    result = {
        "resultVersion": 1,
        "assignmentId": assignment_match.group(1),
        "patchDigest": digests.pop(),
        "verdict": "approved",
        "criteria": [
            {
                "criterionId": "ac1",
                "status": "satisfied",
                "evidenceRefs": list(input_refs),
            }
        ],
        "blockingFindings": [],
        "warnings": [],
        "requestedChecks": [],
        "confidence": "high",
    }
    context._subagent_mailbox.write(  # type: ignore[attr-defined]
        str(input_data["_result_key"]),
        json.dumps(result),
        author="reviewer",
    )


def test_committed_hung_final_audit_is_bounded_and_truthful(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung final audit cannot hide an already committed parent change."""
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    audit_started = threading.Event()
    audit_finished = threading.Event()
    release_audit = threading.Event()
    audit_threads: list[threading.Thread] = []

    class HangingAuditSink:
        def emit(self, *_args, **_kwargs):
            return None

        def record_scheduler_commit_ready(self, _ready):
            return True

        def record_scheduler_audit(self, _audit):
            audit_threads.append(threading.current_thread())
            audit_started.set()
            release_audit.wait()
            audit_finished.set()
            return True

    def worker(input_data, context):
        role = _worker_role(input_data)
        if role == "analyst":
            _write_analyst_result(input_data, context)
        elif role == "executor":
            Path(context.cwd, "allowed.txt").write_text(
                "after",
                encoding="utf-8",
            )
            _write_executor_result(input_data, context)
        else:
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output=f"{role} complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(
        DeterministicVerifier,
        "run",
        lambda self, **request: _verification_result(request),
    )
    # ``raising=False`` keeps these exact test bytes importable on f8ae6fd.
    # That revision accepts the injected attribute but never consults it, so
    # its synchronous recorder remains blocked for the historical counterexample.
    monkeypatch.setattr(
        workflow_runner_module,
        "_FINALIZATION_AUDIT_PORT_RUNNER",
        BoundedConflictPortRunner(
            max_in_flight=1,
            default_timeout_seconds=0.02,
            cancellation_poll_seconds=0.001,
        ),
        raising=False,
    )

    results: list[ToolResult] = []
    errors: list[BaseException] = []
    run_finished = threading.Event()

    def invoke_workflow() -> None:
        try:
            results.append(
                task_module.task_tool.run(
                    task_module.task_tool.validator(
                        {
                            "description": "portable final audit",
                            "prompt": "Apply the structured request.",
                            "agent_type": "workflow",
                            "workflow_request": _request(),
                        }
                    ),
                    ToolContext(
                        cwd=str(tmp_path),
                        _runtime={"model": "fake"},
                        _event_sink=HangingAuditSink(),
                        _central_workflow_v2_enforced=True,
                        _subagent_mailbox=SubagentMailbox(),
                    ),
                )
            )
        except BaseException as error:
            errors.append(error)
        finally:
            run_finished.set()

    workflow_thread = threading.Thread(target=invoke_workflow, daemon=True)
    workflow_thread.start()
    try:
        assert audit_started.wait(5.0)
        started = time.monotonic()
        completed_within_budget = run_finished.wait(1.0)
        elapsed = time.monotonic() - started
        terminal_snapshot = None
        if completed_within_budget and not errors and len(results) == 1:
            returned = results[0]
            terminal_snapshot = (
                returned.ok,
                returned.output,
                returned.awaitUser,
                returned.verification,
                returned._must_deliver,
                returned._termination_reason,
            )
    finally:
        # Release both implementations on every assertion/error path.  On the
        # current implementation this releases only the already-timed-out port
        # thread; on f8ae6fd it also lets the synchronously blocked workflow
        # unwind into an ordinary assertion failure.
        release_audit.set()
        for audit_thread in tuple(audit_threads):
            audit_thread.join(2.0)
        workflow_thread.join(2.0)

    assert audit_finished.is_set()
    assert audit_threads and all(not item.is_alive() for item in audit_threads)
    assert not workflow_thread.is_alive()
    if errors:
        raise errors[0]

    assert completed_within_budget, (
        f"workflow remained blocked on final audit for {elapsed:.3f}s"
    )
    assert len(results) == 1
    result = results[0]
    assert terminal_snapshot is not None

    assert elapsed < 1.0
    assert target.read_text(encoding="utf-8") == "after"
    assert result.ok is False
    assert result._must_deliver is True
    assert '"changesCommitted": true' in result.output
    assert '"auditStatus": "projection_or_persistence_failed"' in result.output
    assert (
        result.ok,
        result.output,
        result.awaitUser,
        result.verification,
        result._must_deliver,
        result._termination_reason,
    ) == terminal_snapshot
