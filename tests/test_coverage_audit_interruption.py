"""Actual native loops retain interrupted audits; no network or real project task."""
from copy import deepcopy
import io
import json

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.completion_coverage_audit import audit_completion_coverage
from minicode.coverage_audit_state import record_document, validate_coverage_audit_state
from minicode.coverage_audit_submission import rejection_view
from minicode.completion_evidence_view import EvidenceViews
from minicode.implementation_completion_review import requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.tooling import ToolContext
from minicode.types import AgentStep
from test_coverage_rejection_recovery import rejection_case, recover
from test_completion_feedback_chronology import (
    inventory as inventory_fixture, COMPLETE, OBJECTIVE, PROBE, _call, _result,
)
from test_implementation_completion_review import _native_coverage_step, _inspection_step

inventory = inventory_fixture


def response(message):
    class Response(io.BytesIO):
        status = 200
    return Response(json.dumps({"choices": [{"message": message,
        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 30}}).encode())


def tool_message(identity, name, value):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": identity,
        "type": "function", "function": {"name": name, "arguments": json.dumps(value)}}]}


@pytest.mark.parametrize("ending", ["timeout", "ordinary_final"])
def test_native_partial_retains_draft_and_actual_page_then_resumes_same_audit(tmp_path, monkeypatch, ending):
    case = rejection_case(tmp_path)
    pending = recover(case).pending_state
    runtime = {"model": "gpt-test", "openaiBaseUrl": "https://openai.invalid/v1",
        "openaiApiKey": "test", "modelMaxRetries": 0, "maxOutputTokens": 2048}
    host = ToolContext(cwd=str(tmp_path), permissions=case["permissions"], _runtime=runtime,
        _agent_budget=AgentTurnBudget())
    requests, snapshots = [], []
    projected = rejection_view(json.loads(case["full"]), EvidenceViews(candidate_digest=case["candidate"]))
    # Replace elapsed waiting only; production retry classification/loop is real.
    monkeypatch.setattr("minicode.agent_loop.controlled_retry_sleep", lambda *_a, **_k: None)

    def transport(request, *, timeout):
        body = json.loads(request.data)
        requests.append(body)
        if len(requests) == 1:
            content = next(m["content"] for m in body["messages"] if m.get("role") == "system"
                and '{"coverageAuditContinuation"' in m.get("content", ""))
            continuation = json.JSONDecoder().raw_decode(
                content[content.index('{"coverageAuditContinuation"'):])[0]["coverageAuditContinuation"]
            assert continuation["latestUnacceptedDraft"]["draftId"] == case["draft"]["draftId"]
            doc = projected["documentRef"]
            return response(tool_message("actual-rejection-page", "completion_evidence_read",
                {"documentId": doc}))
        assert any(m.get("role") == "tool" and m.get("tool_call_id") == "actual-rejection-page"
                   for m in body["messages"])
        if ending == "timeout":
            raise TimeoutError("generic transient provider timeout")
        return response({"role": "assistant", "content": "No native submission yet."})

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)

    def audit(state, model):
        return audit_completion_coverage(objective=case["objective"], requirements=case["units"],
            proposal=case["proposal"], candidate_digest=case["candidate"], model=model,
            tools=case["tools"], cwd=str(tmp_path), permissions=case["permissions"], host=host,
            event_sink=None, persist=case["persist"], checkpoint=lambda: None,
            probe_evidence=lambda _pid: None, source_anchors=case["anchors"],
            continuation_state=state, state_sink=lambda value: snapshots.append(deepcopy(value)))

    outcome = audit(pending, OpenAIModelAdapter(runtime, case["tools"]))
    retained = snapshots[-1]
    assert retained is not None, "A normal API partial must not erase the latest native pending checkpoint."
    assert outcome["reason"] == "coverage_audit_interrupted" and outcome["auditPending"] is True
    assert outcome["continuationTarget"] == "coverage_auditor"
    assert not [kind for kind, _path in case["records"] if kind == "coverage-audit-decision"]
    assert len(requests) == (5 if ending == "timeout" else 2)
    validate_coverage_audit_state(retained, objective=case["objective"], candidate_digest=case["candidate"])
    prior_messages = record_document(pending["messagesRecord"])["messages"]
    retained_messages = record_document(retained["messagesRecord"])["messages"]
    # The lightweight seeded conversation gains its first native Task Ledger.
    original_rows = [m for m in retained_messages if not m.get("_task_ledger")]
    assert original_rows[:len(prior_messages)] == prior_messages
    actual_page = _result(retained_messages, "actual-rejection-page")
    assert actual_page["isError"] is False
    assert record_document(retained["inspectionRecord"])["reads"] == record_document(pending["inspectionRecord"])["reads"]
    # Viewing diagnostics grants no new complete source delivery credit.
    assert record_document(retained["deliveryRecord"])["pairs"] == record_document(pending["deliveryRecord"])["pairs"]
    assert _result(retained_messages, "generic-rejected-coverage")["content"] == case["visible"]
    assert sum(bool(m.get("_model_transport_failure")) for m in retained_messages) == (4 if ending == "timeout" else 0)
    assert sum(bool(m.get("_partial")) for m in retained_messages) == (1 if ending == "timeout" else 0)

    resumed_requests = []

    def resume_transport(request, *, timeout):
        body = json.loads(request.data)
        resumed_requests.append(body)
        assert any(m.get("role") == "tool" and m.get("content") == actual_page["content"]
                   for m in body["messages"])
        assert any(m.get("role") == "tool" and m.get("content") == case["visible"]
                   for m in body["messages"])
        patch = {"draftId": case["draft"]["draftId"], "updates": [
            {"path": "/status", "value": "inconclusive"},
            {"path": "/findings", "value": ["Native semantic assessment remains uncertain."]}]}
        return response(tool_message("explicit-native-conclusion", "completion_coverage_audit_submit", patch))

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", resume_transport)
    accepted = audit(retained, OpenAIModelAdapter(runtime, case["tools"]))
    assert accepted["status"] == "inconclusive" and "auditPending" not in accepted
    assert snapshots[-1] is None, "An accepted native conclusion still consumes the pending audit."
    assert len(resumed_requests) == 1
    assert len([kind for kind, _path in case["records"] if kind == "coverage-audit-decision"]) == 1


def test_parent_pauses_without_rerunning_probes_then_same_owner_resumes_and_approves(inventory, monkeypatch):
    case = inventory
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]
    before_pause = {}
    monkeypatch.setattr("minicode.agent_loop.controlled_retry_sleep", lambda *_a, **_k: None)

    def probe_count():
        return len([row for row in case["commands"] if "test_public.py" not in row["argv"]])

    class Seed:
        parent_calls = reviewer_calls = 0
        rejected = False

        def next(self, messages, **kwargs):
            registry = kwargs["tools_override"]
            inspected = _inspection_step(messages, registry)
            if inspected is not None:
                return inspected
            names = set(registry.list_all())
            if "completion_coverage_audit_submit" in names:
                if self.rejected:
                    assert _result(messages, "rejected-before-network")["isError"]
                    before_pause.update(messages=deepcopy(messages), probes=probe_count())
                    raise KeyboardInterrupt("seed the existing same-owner audit before its network failure")
                step = _native_coverage_step(messages, stable_evidence=True)
                if step.calls[0]["toolName"] == "completion_coverage_audit_submit":
                    step.calls[0]["id"] = "rejected-before-network"
                    step.calls[0]["input"]["findings"] = ["Unsupported approval annotation."]
                    self.rejected = True
                return step
            if "completion_review_submit" in names:
                self.reviewer_calls += 1
                if self.reviewer_calls == 1:
                    return _call("actual-probe", "review_test", purpose="acceptance", criterionIds=ids,
                        scenario="Actual inventory quantity operations.", code=PROBE)
                assert self.reviewer_calls == 2
                receipt = json.JSONDecoder().raw_decode(_result(messages, "actual-probe")["content"])[0]
                assert receipt["receipt"]["status"] == "passed"
                return _call("proposal", "completion_review_submit", status="approved", findings=[],
                    requirements=[{"id": identity, "status": "covered", "probeIds": [receipt["probeId"]],
                        "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
                        "rationale": "The actual quantity assertions passed."} for identity in ids])
            self.parent_calls += 1
            if self.parent_calls == 1:
                return _call("original-edit", "write_file", path="stock.py", content=COMPLETE)
            assert self.parent_calls == 2, "Network failure is not an Executor product-repair turn."
            return AgentStep(type="assistant", kind="final", content="Request completion review.")

    seed = Seed()
    with pytest.raises(KeyboardInterrupt, match="seed the existing same-owner audit"):
        run_agent_turn(model=seed, messages=[{"role": "user", "content": OBJECTIVE}], **case["options"])
    seed_document, seed_messages = deepcopy(case["checkpoints"][-1])
    seed_continuation = HostImplementationContinuation.from_document(seed_document,
        spec=case["spec"], messages=seed_messages)

    class Pause:
        calls = 0

        def next(self, actual, **kwargs):
            self.calls += 1
            assert "completion_coverage_audit_submit" in kwargs["tools_override"].list_all()
            assert actual[:len(before_pause["messages"])] == before_pause["messages"]
            raise TimeoutError("generic auditor provider timeout")

    pause = Pause()
    run_agent_turn(model=pause, messages=seed_messages, implementation_continuation=seed_continuation,
        **case["options"])
    document, messages = deepcopy(case["checkpoints"][-1])
    pending = document["reviewState"]["pendingCoverageAudit"]
    assert pending is not None
    assert pause.calls == 4 and seed.parent_calls == 2
    assert probe_count() == before_pause["probes"], "No receipt refresh before unfinished coverage audit resumes."
    retained = record_document(pending["messagesRecord"])["messages"]
    assert retained[:len(before_pause["messages"])] == before_pause["messages"]
    paused = [row["payload"] for row in case["events"] if row["type"] == "implementation.completion_decided"][-1]
    assert paused["status"] == "inconclusive" and paused["reason"] == "coverage_audit_interrupted"
    assert paused["resumable"] and paused["auditPending"] and not paused["canContinue"]
    assert paused["continuationTarget"] == "coverage_auditor" and paused["repairProbes"] == []
    assert all(row["status"] == "unknown" for row in paused["requirements"])
    feedback = next(m for m in messages if m.get("_implementation_completion_feedback"))
    original_feedback = feedback.get("_completion_evidence_original", feedback["content"])
    assert "Resume that audit" in original_feedback and "Repair the evidenced gaps" not in original_feedback
    restored = HostImplementationContinuation.from_document(document, spec=case["spec"], messages=messages)

    class Resume:
        auditor_calls = parent_calls = 0

        def next(self, actual, **kwargs):
            names = set(kwargs["tools_override"].list_all())
            assert "completion_coverage_inspection_submit" not in names
            assert "completion_review_submit" not in names
            if "completion_coverage_audit_submit" in names:
                self.auditor_calls += 1
                assert self.auditor_calls == 1, "Already delivered source pages must not be reread."
                assert actual[:len(retained)] == retained
                assert probe_count() == before_pause["probes"]
                step = _native_coverage_step(actual, stable_evidence=True)
                assert step.calls[0]["toolName"] == "completion_coverage_audit_submit"
                return step
            self.parent_calls += 1
            assert _result(actual, "original-edit")["isError"] is False
            return AgentStep(type="assistant", kind="final", content="Verified quantities are ready.")

    resume = Resume()
    output = run_agent_turn(model=resume, messages=messages, implementation_continuation=restored, **case["options"])
    assert resume.auditor_calls == 1 and resume.parent_calls == 1
    assert probe_count() > before_pause["probes"], "Final validation remains mandatory after native audit approval."
    assert (case["workspace"] / "stock.py").read_text() == COMPLETE
    decisions = [row["payload"] for row in case["events"] if row["type"] == "implementation.completion_decided"]
    assert [row["status"] for row in decisions] == ["inconclusive", "approved", "approved"]
    assert {row["executorId"] for row in decisions} == {document["executorId"]}
    assert output[-1]["role"] == "assistant"


def test_initial_inspection_without_native_commit_stays_pending(tmp_path):
    from test_coverage_audit_recovery import _case

    case = _case(tmp_path)
    snapshots = []

    class Model:
        def next(self, _messages, **kwargs):
            assert "completion_coverage_inspection_submit" in kwargs["tools_override"].list_all()
            return AgentStep(type="assistant", kind="final", content="Inspection has no committed result.")

    outcome = audit_completion_coverage(objective=case["objective"], requirements=case["units"],
        proposal=case["proposal"], candidate_digest=case["candidate"], model=Model(),
        tools=case["tools"], cwd=str(tmp_path), permissions=case["permissions"], host=case["context"],
        event_sink=None, persist=case["persist"], checkpoint=lambda: None,
        probe_evidence=lambda _pid: None, source_anchors=case["anchors"],
        state_sink=lambda value: snapshots.append(deepcopy(value)))
    assert outcome["reason"] == "coverage_audit_interrupted" and outcome["auditPending"] is True
    assert outcome["inspectionPhase"] == "inspection"
    retained = snapshots[-1]
    assert retained is not None
    inspection = record_document(retained["inspectionRecord"])
    assert inspection["result"] is None and inspection["phase"] == "inspection"
    assert inspection["reads"] == []
    assert not [kind for kind, _path in case["records"] if kind == "coverage-audit-decision"]
