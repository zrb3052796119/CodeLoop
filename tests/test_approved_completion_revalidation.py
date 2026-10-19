"""Host reopening preserves the actual owner while refusing stale approvals."""
from copy import deepcopy
import subprocess
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_continuation import HostImplementationContinuation, continuation_digest
from minicode.implementation_execution import implementation_candidate_digest
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep
from test_implementation_continuation import COMPLETE, OBJECTIVE, PARTIAL, TESTS, specification, write_step


def checkpoint(tmp_path):
    (tmp_path / "stock.py").write_text(PARTIAL)
    (tmp_path / "test_stock.py").write_text(TESTS)
    spec = specification()
    context = ToolContext(cwd=str(tmp_path))
    digest = implementation_candidate_digest(spec, context)
    review = ImplementationCompletionReview(spec)
    approval = {"status": "approved", "patchDigest": digest, "findings": [],
                "evidenceRef": str(tmp_path / "original-approval.json")}
    review._reviews.append(approval)
    messages = [{"role": "user", "content": OBJECTIVE},
                {"role": "tool_result", "toolUseId": "original-edit", "content": "Existing edit saved."},
                {"role": "assistant", "content": "Previously reported complete."}]
    native = HostImplementationContinuation.capture(spec=spec, context=context,
        messages=messages, executor_id="implementation_" + "a" * 32, attempt=4, review_state=review.state)
    document = native.document
    request = {"revalidationVersion": 1, "reason": "public_requirement_revalidation",
        "checkpointDigest": continuation_digest(document),
        **{key: document[key] for key in ("executorId", "attempt", "candidateDigest", "specDigest", "messagesDigest")},
        "approvalEvidenceRef": approval["evidenceRef"], "approvalDigest": continuation_digest(approval)}
    return spec, document, messages, request


@pytest.mark.parametrize("key", ["checkpointDigest", "executorId", "attempt", "candidateDigest",
    "specDigest", "messagesDigest", "approvalEvidenceRef", "approvalDigest", "reason", "externalResult"])
def test_revalidation_cannot_rebind_or_include_external_judgment(tmp_path, key):
    spec, document, messages, request = checkpoint(tmp_path)
    request[key] = "unrelated"
    with pytest.raises(ValueError, match="revalidation request"):
        HostImplementationContinuation.for_revalidation(document, spec=spec, messages=messages, request=request)


def test_revalidation_preserves_approved_history_and_invalidates_cached_authority(tmp_path):
    spec, document, messages, request = checkpoint(tmp_path)
    original = deepcopy(document)
    resumed = HostImplementationContinuation.for_revalidation(document, spec=spec, messages=messages, request=request)
    assert resumed.requires_revalidation
    assert resumed.document == original
    request["reason"] = "mutated"
    exposed = resumed.revalidation_request
    exposed["reason"] = "mutated"
    assert resumed.revalidation_request["reason"] == "public_requirement_revalidation"
    review = resumed.restore_review(spec)
    assert review.state["reviews"] == original["reviewState"]["reviews"]
    assert review._cached == {}
    assert not HostImplementationContinuation.from_document(original, spec=spec, messages=messages).requires_revalidation


def test_revalidation_reviews_before_parent_and_repairs_same_owner_with_actual_checks(tmp_path, monkeypatch):
    from minicode.deterministic_verifier import DeterministicVerifier
    from minicode.permissions import PermissionManager
    import minicode.workflow_runner as runner

    spec, document, messages, request = checkpoint(tmp_path)
    events, snapshots, commands = [], [], []

    def command(argv, **kwargs):
        result = subprocess.run([sys.executable, *argv[1:]], **kwargs)
        commands.append((str(kwargs["cwd"]), result.returncode))
        return result

    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)

    def assess(self, *, checks_receipt, **kwargs):
        events.append("review")
        assert checks_receipt["status"] == "passed"
        complete = (tmp_path / "stock.py").read_text() == COMPLETE
        complete &= "test_release" in (tmp_path / "test_stock.py").read_text()
        decision = {"status": "approved" if complete else "changes_required",
            "patchDigest": checks_receipt["patchDigest"],
            "findings": [] if complete else ["Original release behavior is still absent."],
            "evidenceRef": str(tmp_path / f"review-{len(events)}.json")}
        self._reviews.append(decision)
        return decision

    monkeypatch.setattr(ImplementationCompletionReview, "assess", assess)

    class Model:
        count = 0

        def next(self, received, **kwargs):
            self.count += 1
            assert events[0] == "review"
            if self.count == 1:
                retained = [m for m in received if not m.get("_task_ledger")]
                assert retained[:len(messages)] == messages
                assert (tmp_path / "stock.py").read_text() == PARTIAL
                assert any(m.get("toolUseId") == "original-edit" for m in received)
                assert any(m.get("_implementation_completion_feedback") for m in received)
                return write_step("repair", "stock.py", COMPLETE)
            if self.count == 2:
                return write_step("regression", "test_stock.py", TESTS +
                                  "def test_release(): assert release(7, 3) == 10\n")
            return AgentStep(type="assistant", kind="final", content="Both operations delivered.")

    resumed = HostImplementationContinuation.for_revalidation(document, spec=spec, messages=messages, request=request)
    result = run_agent_turn(model=Model(), tools=ToolRegistry([write_file_tool]), cwd=str(tmp_path),
        messages=deepcopy(messages), implementation_spec=spec, implementation_continuation=resumed,
        implementation_task=True, enable_work_chain=False, max_steps=None, agent_budget=AgentTurnBudget(),
        implementation_checkpoint_sink=lambda doc, msgs: snapshots.append((doc, msgs)))
    assert result[-1]["content"] == "Both operations delivered."
    assert events == ["review", "review"]
    assert snapshots[-1][0]["executorId"] == document["executorId"]
    assert snapshots[-1][0]["attempt"] == document["attempt"] + 2
    assert snapshots[-1][0]["reviewState"]["reviews"][0] == document["reviewState"]["reviews"][0]
    assert commands and all(cwd != str(tmp_path) and code == 0 for cwd, code in commands)
