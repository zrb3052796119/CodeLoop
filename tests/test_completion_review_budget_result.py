"""Budget closure preserves current native evidence without another execution."""

from copy import deepcopy
from dataclasses import replace
import json
import socket
import time

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.completion_review_limits import create_review_limits
from minicode.coverage_audit_state import record_document
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_execution import implementation_candidate_digest
from minicode.task_deadline import TaskDeadlineExceeded
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool
from test_completion_failure_adjudication import judge
from test_implementation_completion_review import _fixture, _probe, _rows, _submit, PROBE, SOURCE


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail("Budget result tests must not contact a provider")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def _cutoff():
    return AgentBudgetExceeded("completion review stage allowance exhausted",
        AgentTurnBudget(max_model_calls=1).snapshot())


def _close(review, digest, context, commands, monkeypatch):
    state, count = deepcopy(review.state), len(commands)
    with monkeypatch.context() as patch:
        patch.setattr("minicode.agent_loop.run_agent_turn",
            lambda **_kwargs: pytest.fail("Budget closure cannot run a model"))
        result = review.review_budget_exhausted_result(digest, context, str(_cutoff()))
    assert review.state == state
    assert len(commands) == count
    assert "completionReview" not in result
    return result


def _attributed_case(tmp_path, monkeypatch, verdict):
    review, assess, commands, capability = _fixture(tmp_path, monkeypatch,
        adjudication_driver=judge(verdict,
            reason="The objective requires adding the amount; the source returns the unchanged quantity."))
    (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n"))

    def ask_for_attribution(tools, child, packet):
        failed, _ = _probe(tools, child, packet)
        assert not failed.ok
        assert _submit(tools, child, packet, status="inconclusive",
            rows=_rows(packet, status="unknown"), findings=["The failed assertion needs attribution."]).ok

    try:
        prior = assess(ask_for_attribution)
    except BaseException:
        capability.close()
        raise
    assert review.state.get("acceptedRepairDecision") is None
    return review, prior, commands, capability


def test_budget_after_native_missing_keeps_repair_and_unattributed_probe(tmp_path, monkeypatch):
    review, assess, commands, capability = _fixture(tmp_path, monkeypatch)
    (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n"))
    cutoff = _cutoff()

    def submit_then_stop(tools, child, packet):
        failed, _ = _probe(tools, child, packet,
            "from stock import hold\ndef test_unattributed():\n    assert hold(10, 3) == 8\n")
        assert not failed.ok
        submitted = _submit(tools, child, packet, status="changes_required",
            rows=_rows(packet, status="missing"), findings=["release does not add its amount."])
        assert submitted.ok
        # The native commit occurred, but assess has not published a final review.
        raise cutoff

    try:
        with pytest.raises(AgentBudgetExceeded) as caught:
            assess(submit_then_stop)
        assert caught.value is cutoff and review.state["reviews"] == []
        committed = record_document(review.state["acceptedRepairDecision"])["decision"]
        context = ToolContext(cwd=str(tmp_path))
        result = _close(review, committed["patchDigest"], context, commands, monkeypatch)
        assert result["status"] == "changes_required"
        assert result["findings"] == committed["findings"]
        assert result["requirements"] == committed["requirements"]
        assert result["probeReceipts"] == committed["probeReceipts"]
        assert result["unresolvedProbes"] == committed["unresolvedProbes"]
        assert result["unresolvedProbes"] and result["repairProbes"] == []
        assert result["repairDecisionRef"] == review.state["acceptedRepairDecision"]["ref"]
    finally:
        capability.close()


@pytest.mark.parametrize("verdict", ["product_gap", "inconclusive"])
def test_budget_result_reuses_only_current_confirmed_product_gap(tmp_path, monkeypatch, verdict):
    review, prior, commands, capability = _attributed_case(tmp_path, monkeypatch, verdict)
    try:
        context = ToolContext(cwd=str(tmp_path))
        result = _close(review, prior["patchDigest"], context, commands, monkeypatch)
        assert result["failureAdjudications"] == review.state["failureAdjudications"]
        assert result["probeReceipts"]
        if verdict == "product_gap":
            assert result["status"] == "changes_required"
            assert result["reason"] == "confirmed_product_gap"
            assert [probe["code"] for probe in result["repairProbes"]] == [PROBE]
        else:
            assert result["status"] == "inconclusive"
            assert result["reason"] == "completion_review_budget_exhausted"
            assert result["canContinue"] is False and result["repairProbes"] == []
            assert result["unresolvedProbes"]
    finally:
        capability.close()


def test_changed_candidate_cannot_reuse_old_product_gap(tmp_path, monkeypatch):
    review, prior, commands, capability = _attributed_case(tmp_path, monkeypatch, "product_gap")
    try:
        (tmp_path / "stock.py").write_text(SOURCE)
        context = ToolContext(cwd=str(tmp_path))
        current = implementation_candidate_digest(review.spec, context)
        assert current != prior["patchDigest"]
        stale = _close(review, prior["patchDigest"], context, commands, monkeypatch)
        assert stale["reason"] == "completion_candidate_changed"
        assert stale["status"] == "inconclusive" and stale["patchDigest"] == current
        result = _close(review, current, context, commands, monkeypatch)
        assert result["reason"] == "completion_review_budget_exhausted"
        assert result["repairProbes"] == [] and result["probeReceipts"] == []
    finally:
        capability.close()


def test_pending_audit_survives_budget_result_and_resumes_same_evidence(tmp_path, monkeypatch):
    cutoff = _cutoff()

    def stop_audit(*_args):
        raise cutoff

    review, assess, commands, capability = _fixture(tmp_path, monkeypatch, coverage_driver=stop_audit)

    def propose(tools, child, packet):
        passed, receipt = _probe(tools, child, packet)
        assert passed.ok
        assert _submit(tools, child, packet, ids=[receipt["probeId"]]).ok

    try:
        with pytest.raises(AgentBudgetExceeded) as caught:
            assess(propose)
        assert caught.value is cutoff
        saved = deepcopy(review.state)
        audit = saved["pendingCoverageAudit"]
        assert record_document(audit["inspectionRecord"])["phase"] == "reconciliation"
        result = _close(review, audit["candidateDigest"], ToolContext(cwd=str(tmp_path)), commands, monkeypatch)
        assert result["status"] == "inconclusive"
        assert result["reason"] == "completion_review_budget_exhausted"
        assert result["canContinue"] is False and result["resumable"] is True
        assert result["auditPending"] is True and result["continuationTarget"] == "coverage_auditor"
        assert result["evidenceDirectory"] == saved["evidenceDirectory"]
        assert review.state["pendingCoverageAudit"] == audit
    finally:
        capability.close()

    restored, resume, _, capability = _fixture(tmp_path, monkeypatch,
        review_factory=lambda spec: ImplementationCompletionReview.from_state(spec, saved))
    try:
        result = resume(lambda *_: pytest.fail("Resume must not regenerate ordinary reviewer proposal"))
        assert result["status"] == "approved"
        assert restored.state["pendingCoverageAudit"] is None
        assert result["coverageAudit"]["inspectionRecord"] == audit["inspectionRecord"]
    finally:
        capability.close()


def test_budget_result_does_not_hide_expired_parent_deadline(tmp_path, monkeypatch):
    review, _assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    try:
        digest = implementation_candidate_digest(review.spec, ToolContext(cwd=str(tmp_path)))
        expired = ToolContext(cwd=str(tmp_path), _task_deadline_monotonic=time.monotonic() - 1)
        with pytest.raises(TaskDeadlineExceeded):
            review.review_budget_exhausted_result(digest, expired, str(_cutoff()))
    finally:
        capability.close()


@pytest.mark.parametrize("mode,verdict,retained,interrupted", [
    ("bounded", "product_gap", True, True),
    ("bounded", "inconclusive", False, True),
    ("full", "product_gap", False, True),
    ("bounded", "product_gap", True, False),
])
def test_native_attribution_retains_bounded_product_gap_at_commit_without_duplication(
        tmp_path, monkeypatch, mode, verdict, retained, interrupted):
    review, assess, commands, capability = _fixture(tmp_path, monkeypatch)
    (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n"))

    def seed_failed_probe(tools, child, packet):
        failed, _ = _probe(tools, child, packet)
        assert not failed.ok

    try:
        prior = assess(seed_failed_probe)
        assert prior["status"] == "inconclusive" and not review.state["failureAdjudications"]
        monkeypatch.setenv("MINI_CODE_COMPLETION_REVIEW_MODE", mode)
        parent = ToolContext(cwd=str(tmp_path), _agent_budget=AgentTurnBudget(),
            _task_deadline_monotonic=time.monotonic() + 600)
        context = replace(parent, _completion_review_limits=create_review_limits(parent))
        cutoff = TaskDeadlineExceeded(90)
        submissions = []

        def submit_then_deadline(**values):
            assert "completion_probe_adjudication_submit" in values["tools"].list_all()
            child = ToolContext(cwd=values["cwd"], permissions=values["permissions"])
            packet = json.loads(values["messages"][1]["content"])
            result = judge(verdict, reason="The objective requires release to add its amount.")(
                values["tools"], child, packet)
            submissions.append(verdict)
            # Simulate the post-submit native deadline boundary, before assess
            # can return the verdict to the enclosing completion review.
            if interrupted:
                raise cutoff
            return result

        monkeypatch.setattr("minicode.agent_loop.run_agent_turn", submit_then_deadline)
        arguments = dict(model=object(), tools=ToolRegistry([read_file_tool]), context=context,
            agent_budget=parent._agent_budget, event_sink=None, cancellation_token=None,
            deadline_monotonic=parent._task_deadline_monotonic,
            checks_receipt=capability.ensure_completion_checks(parent))
        if interrupted:
            with pytest.raises(TaskDeadlineExceeded) as caught:
                review.assess(**arguments)
            assert caught.value is cutoff
        else:
            assert review.assess(**arguments)["reason"] == "confirmed_product_gap"
        assert submissions == [verdict]
        authorities = review.state["failureAdjudications"]
        assert len(authorities) == int(retained)
        assert not any(row["status"] == "approved" for row in review.state["reviews"])
        result = _close(review, prior["patchDigest"], parent, commands, monkeypatch)
        if retained:
            assert result["status"] == "changes_required" and result["reason"] == "confirmed_product_gap"
            assert result["failureAdjudications"] == authorities
            assert [probe["code"] for probe in result["repairProbes"]] == [PROBE]
            restored = ImplementationCompletionReview.from_state(review.spec, review.state)
            assert restored.state["failureAdjudications"] == authorities
        else:
            assert result["status"] == "inconclusive" and result["repairProbes"] == []
    finally:
        capability.close()
