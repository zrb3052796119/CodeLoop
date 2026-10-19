"""New renderer/issuer boundaries, separate from the old-production loop proof."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_workflow_scoped_budget_delivery import _exercise

import minicode.workflow_finalization as finalization
from minicode.turn_completion import workflow_delivery_summary


@pytest.mark.parametrize("secret_argument", [
    ("--api-key", "UNIQUE_API_SECRET_9db3"), ("--token=UNIQUE_API_SECRET_9db3",),
])
def test_delivery_summary_redacts_command_secrets_in_text_and_metadata(secret_argument):
    # Pure rendering data cannot authorize a result or mint a receipt.
    receipt = SimpleNamespace(workflow_id="wf_render", task_id="task_render", patch_digest="sha256:" + "a" * 64,
        files=(("app.py", "modified"),), checks=(("public_check", ("python", "check.py", *secret_argument)),))
    text, metadata = workflow_delivery_summary([receipt], remaining=[], reason="token budget exhausted")
    assert "UNIQUE_API_SECRET_9db3" not in text + json.dumps(metadata)
    assert "[REDACTED]" in text and "[REDACTED]" in json.dumps(metadata)
    assert metadata["overallGoal"] == "not_assessed" and metadata["delivery"] == "delivered"
    assert "overallGoal" not in text and "not_assessed" not in text
    assert "argv" not in metadata["workflows"][0]["verification"][0]
    assert receipt.checks[0][1][-1].endswith("UNIQUE_API_SECRET_9db3"), "Rendering must not mutate canonical evidence"


def test_real_audited_candidate_can_deliver_but_pending_feedback_cannot_mint_receipt(tmp_path, monkeypatch):
    original = finalization.issue_workflow_delivery
    observed = []

    def inspect_real_finalization(snapshot, state):
        document = json.loads(snapshot.execution_artifact_json)
        assert document["resultType"] == "ExecutionCandidate.v1"
        assert document["status"] == "awaiting_verification"
        assert document["handoffReason"] == "executor_budget_exceeded"
        assert snapshot.scheduler_outcome.committed
        assert snapshot.commit_ready_status == "persisted" and state.audit_status == "persisted"
        for pending in ("executor_write_feedback_pending", "executor_submission_feedback_pending"):
            invalid = {**document, "handoffReason": pending}
            assert original(replace(snapshot, execution_artifact_json=json.dumps(invalid)), state) is None
        receipt = original(snapshot, state)
        assert receipt is not None
        observed.append(receipt)
        return receipt

    monkeypatch.setattr(finalization, "issue_workflow_delivery", inspect_real_finalization)
    messages, calls, _, capture, _ = _exercise(tmp_path, monkeypatch, candidate_handoff=True)
    assert len(observed) == 1 and len(calls) == 9
    assert messages[-1].get("_workflow_delivery_receipt_summary") is True
    assert messages[-1]["_partial"] is False
    assert messages[-1]["_workflow_delivery"]["overallGoal"] == "not_assessed"
    assert capture.outcome.goal_achieved is False
