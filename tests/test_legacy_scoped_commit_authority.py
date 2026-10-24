"""Legacy scoped plans retain real verifier authority without weakening v6."""

from dataclasses import replace
import json

import pytest

from minicode.central_scheduler import CentralScheduler
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.workflow_runner import _WorkflowRunSession
from test_executor_edit_failure_delivery import _exercise


@pytest.mark.parametrize("tamper", ["current_policy", "stale_candidate", "missing_checks"])
def test_legacy_commit_cannot_bypass_current_review_or_exact_verifier_evidence(
    tmp_path, monkeypatch, tamper,
):
    prepare = _WorkflowRunSession.prepare_commit
    canonical_plan = CentralScheduler.plan
    attempts = []

    def guarded_prepare(self, request):
        state = self._execution_state()
        assert request.rule_id == "route.scoped_change"
        assert state.verification_result["status"] == "passed"
        assert state.frozen_workspace_change.patch_digest == request.patch_digest
        assert state.change_diff_artifact["complete"] is True
        read = state.artifact_store.read
        actual = read(state.verification_artifact_id)
        assert json.loads(actual.content) == state.verification_result

        with monkeypatch.context() as patch:
            if tamper == "current_policy":
                # A legacy-looking rule cannot waive current review policy.
                patch.setattr(CentralScheduler, "plan", lambda owner, profile, **_kwargs:
                    canonical_plan(owner, replace(profile, independent_review_required=True),
                                   policy_version=6))
            else:
                document = json.loads(actual.content)
                if tamper == "stale_candidate":
                    document["patchDigest"] = "sha256:" + "0" * 64
                else:
                    document["checks"] = []
                changed = replace(actual, content=json.dumps(document))
                patch.setattr(state.artifact_store, "read", lambda reference:
                    changed if reference == state.verification_artifact_id else read(reference))
            accepted = prepare(self, request)
        attempts.append(accepted)
        assert accepted is False
        assert state.commit_ready_status == "projection_failed"
        return accepted

    monkeypatch.setattr(_WorkflowRunSession, "prepare_commit", guarded_prepare)
    result, _calls, commands, _models, _budget, target, initial, _data_dir = _exercise(tmp_path, monkeypatch)
    assert attempts == [False]
    assert commands[-1]["exit_code"] == 0, "The host must reject even after genuine checks passed."
    assert not result.ok and target.read_text() == initial
    trace = extract_scheduler_trace(result.output)
    assert trace["committed"] is False
    assert trace["terminalReason"] == "commit_ready_persistence_failed"
