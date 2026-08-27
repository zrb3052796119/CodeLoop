"""Public workflow integration contract for the V2 central scheduler.

These tests keep the legacy ``task(agent_type="workflow")`` interface at the
outside while requiring all mutation authority to pass through the scheduler,
the isolated transaction, deterministic verification, and the review gate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

import minicode.tools.task as task_module
from minicode.central_scheduler import CentralScheduler
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.prompt import build_system_prompt
from minicode.subagent_mailbox import SubagentMailbox
from minicode.subagent_result import (
    extract_subagent_result,
    project_subagent_result,
    render_subagent_result,
)
from minicode.tooling import ToolContext, ToolResult
from minicode.workflow_workspace import (
    FrozenWorkflowChange,
    WorkflowWorkspaceTransaction,
)


@dataclass
class _WorkflowRun:
    result: ToolResult
    events: list[str]
    change_sets: list[dict[str, object]]
    verifier_calls: list[dict[str, object]]
    promotion_calls: list[dict[str, object]]
    target: Path


def _phase_result(
    *,
    ordinal: int,
    agent_type: str,
    summary: str,
    action: str,
) -> ToolResult:
    report = json.dumps(
        {
            "resultVersion": 1,
            "summary": summary,
            "files": [{"path": "target.txt", "action": action}],
            "risks": [],
            # Worker prose is deliberately positive. The scheduler must still
            # treat DeterministicVerifier as the authoritative verification.
            "verification": {
                "status": "passed" if agent_type == "general" else "not_run",
                "checks": ["worker self-check"] if agent_type == "general" else [],
            },
        }
    )
    projected = project_subagent_result(
        report,
        subagent_id="sub_" + f"{ordinal:032x}",
        agent_type=agent_type,
        outcome="completed",
        fallback_summary="unused",
    )
    return ToolResult(
        ok=True,
        output=summary + "\n\n" + render_subagent_result(projected),
    )


def _run_legacy_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    verifier_status: str,
) -> _WorkflowRun:
    target = tmp_path / "target.txt"
    target.write_text("before", encoding="utf-8")
    mailbox = SubagentMailbox()
    events: list[str] = []
    change_sets: list[dict[str, object]] = []
    verifier_calls: list[dict[str, object]] = []
    promotion_calls: list[dict[str, object]] = []
    phase_ordinal = 0

    def fake_child_run(input_data: dict, context: ToolContext) -> ToolResult:
        nonlocal phase_ordinal
        phase_ordinal += 1
        agent_type = input_data["agent_type"]
        prompt = input_data["prompt"]
        if agent_type == "general":
            events.append("agent:executor")
            Path(context.cwd, "target.txt").write_text("after", encoding="utf-8")
            return _phase_result(
                ordinal=phase_ordinal,
                agent_type=agent_type,
                summary="execution complete",
                action="modified",
            )
        if "review verdict key" in prompt:
            events.append("agent:reviewer")
            match = re.search(r"review verdict key `([^`]+)`", prompt)
            assert match is not None
            mailbox.write(
                match.group(1),
                json.dumps(
                    {
                        "reviewVersion": 1,
                        "verdict": "approved",
                        "blockingFindings": [],
                        "warnings": [],
                    }
                ),
                author="reviewer",
            )
            return _phase_result(
                ordinal=phase_ordinal,
                agent_type=agent_type,
                summary="review approved",
                action="read",
            )
        events.append("agent:analyst")
        return _phase_result(
            ordinal=phase_ordinal,
            agent_type=agent_type,
            summary="analysis complete",
            action="read",
        )

    original_execute = CentralScheduler.execute

    def scheduler_execute_spy(self, profile, **ports):
        events.append("scheduler:execute")
        return original_execute(self, profile, **ports)

    original_freeze = WorkflowWorkspaceTransaction.freeze

    def freeze_spy(
        self,
        *,
        allowed_paths,
        paths_relative_to_cwd=False,
    ):
        frozen = original_freeze(
            self,
            allowed_paths=allowed_paths,
            paths_relative_to_cwd=paths_relative_to_cwd,
        )
        events.append("transaction:freeze")
        change_sets.append(frozen.change_set_document)
        return frozen

    def deterministic_verifier_spy(self, **request):
        del self
        events.append("deterministic:verify")
        verifier_calls.append(request)
        check_results = []
        for check in request.get("checks", ()):
            check_results.append(
                {
                    "checkId": check.check_id,
                    "kind": check.kind,
                    "status": verifier_status,
                    "exitCode": 0 if verifier_status == "passed" else 1,
                    "durationMs": 1,
                    "artifactRef": "artifact_" + check.check_id,
                }
            )
        if not check_results:
            check_results.append(
                {
                    "checkId": "check_legacy_workflow",
                    "kind": "tests",
                    "status": verifier_status,
                    "exitCode": 0 if verifier_status == "passed" else 1,
                    "durationMs": 1,
                    "artifactRef": "artifact_legacy_verification",
                }
            )
        return {
            "resultType": "VerificationResult.v1",
            "verificationVersion": 1,
            "verificationId": "verification_legacy_workflow",
            "workflowId": request["workflow_id"],
            "taskId": request["task_id"],
            "executionAssignmentId": request["execution_assignment_id"],
            "patchDigest": request["patch_digest"],
            "status": verifier_status,
            "checks": check_results,
        }

    original_promote = FrozenWorkflowChange.promote

    def promote_spy(
        self,
        *,
        approved_patch_digest,
    ):
        events.append("transaction:promote")
        promotion_calls.append(
            {
                "approved_patch_digest": approved_patch_digest,
            }
        )
        return original_promote(
            self,
            approved_patch_digest=approved_patch_digest,
        )

    monkeypatch.setattr(task_module, "_run", fake_child_run)
    monkeypatch.setattr(CentralScheduler, "execute", scheduler_execute_spy)
    monkeypatch.setattr(
        WorkflowWorkspaceTransaction,
        "freeze",
        freeze_spy,
    )
    monkeypatch.setattr(DeterministicVerifier, "run", deterministic_verifier_spy)
    monkeypatch.setattr(
        FrozenWorkflowChange,
        "promote",
        promote_spy,
    )

    # This is the pre-V2 caller shape. No new task-tool input is required.
    result = task_module.task_tool.run(
        {
            "description": "scheduler integration path",
            "prompt": "Update target.txt and verify the result.",
            "agent_type": "workflow",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _subagent_mailbox=mailbox,
        ),
    )
    return _WorkflowRun(
        result=result,
        events=events,
        change_sets=change_sets,
        verifier_calls=verifier_calls,
        promotion_calls=promotion_calls,
        target=target,
    )


def test_legacy_workflow_commits_only_a_scheduler_verified_frozen_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run_legacy_workflow(
        tmp_path,
        monkeypatch,
        verifier_status="passed",
    )

    assert run.result.ok is True
    structured = extract_subagent_result(run.result.output)
    assert structured is not None
    assert structured["agentType"] == "workflow"
    assert structured["contractStatus"] == "derived"
    assert "[Workflow scheduler integration path completed]" in run.result.output

    assert run.events.index("scheduler:execute") < run.events.index("agent:analyst")
    assert run.events.index("agent:analyst") < run.events.index("agent:executor")
    assert run.events.index("agent:executor") < run.events.index(
        "transaction:freeze"
    )
    assert run.events.index("transaction:freeze") < run.events.index(
        "deterministic:verify"
    )
    assert run.events.index("deterministic:verify") < run.events.index(
        "agent:reviewer"
    )
    assert run.events.index("agent:reviewer") < run.events.index(
        "transaction:promote"
    )

    frozen_digest = run.change_sets[0]["patchDigest"]
    assert run.verifier_calls[0]["patch_digest"] == frozen_digest
    assert run.promotion_calls == [
        {
            "approved_patch_digest": frozen_digest,
        }
    ]
    assert run.target.read_text(encoding="utf-8") == "after"


def test_failed_deterministic_verification_stops_review_and_parent_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run_legacy_workflow(
        tmp_path,
        monkeypatch,
        verifier_status="failed",
    )

    assert run.result.ok is False
    structured = extract_subagent_result(run.result.output)
    assert structured is not None
    assert structured["agentType"] == "workflow"
    assert structured["contractStatus"] == "derived"
    assert "[Workflow scheduler integration path failed]" in run.result.output

    assert run.events.count("scheduler:execute") == 1
    assert run.events.index("agent:executor") < run.events.index(
        "transaction:freeze"
    )
    assert run.events.index("transaction:freeze") < run.events.index(
        "deterministic:verify"
    )
    assert "agent:reviewer" not in run.events
    assert "transaction:promote" not in run.events
    assert run.promotion_calls == []
    assert run.target.read_text(encoding="utf-8") == "before"


def test_public_prompt_and_task_schema_advertise_only_the_central_mutation_path(
    tmp_path: Path,
) -> None:
    prompt = build_system_prompt(str(tmp_path))
    task_description = task_module.task_tool.description
    agent_type_description = task_module.task_tool.input_schema["properties"][
        "agent_type"
    ]["description"]

    assert "agent_type='workflow'" in prompt
    assert "complete WorkflowRequest.v1" in prompt
    assert "agent_type='general' is a deprecated" in prompt
    assert "A production workflow must provide WorkflowRequest.v1" in task_description
    assert "legacy-only 'general'" in agent_type_description
