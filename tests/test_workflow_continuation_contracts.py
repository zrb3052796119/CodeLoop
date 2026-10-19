"""A host continuation quote never replaces failed evidence or final gates.

These are contract/audit tests, not model-quality or budget-consumption runs.
The full audit fixture first earns a real legacy workflow audit with pytest,
then exercises the new trace/document representation through the public audit.
"""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from minicode.central_scheduler import CentralScheduler
from minicode.scheduler_audit import SchedulerAuditValidationError, project_scheduler_audit
from minicode import scheduler_audit
from minicode.scheduler_trace import project_scheduler_trace, render_scheduler_trace
from minicode.workflow_repair import parse_implementation_repair_request
from test_scheduler_repair_trace import repair_inputs
from test_workflow_implementation_repair import _repair_run


CONTINUATION = "implementation_continuation_requested"


def admission(index=1):
    return {
        "continuationVersion": 1, "sessionId": "execsession_contract",
        "limitModelCalls": 40, "limitTotalTokens": 500000,
        "usedModelCalls": index * 2, "usedTotalTokens": index * 10000,
        "reservedTotalTokens": 0, "preserveModelCalls": 10,
        "preserveTotalTokens": 74000, "projectedInputTokens": 12000,
        "outputTokens": 4096, "deadlineMonotonic": 1800.0,
        "admittedAtMonotonic": index * 100.0,
    }


def repair_document(index=1, *, continuation=True):
    result = {
        "repairVersion": 1, "resultType": "ImplementationRepairRequest.v1",
        "workflowId": "wf_continuation", "taskId": "task_continuation",
        "assignmentId": f"asg_repair_{index}", "repairIndex": index,
        "sourceStage": "verifier", "sourceArtifactRef": "artifact_verification",
        "requestArtifactRef": "artifact_request", "previousExecutionArtifactRef": "artifact_execution",
        "previousOrderRef": "order_previous", "previousDiffArtifactRef": "artifact_diff",
        "previousPatchDigest": "sha256:" + "a" * 64,
        "previousVerificationArtifactRef": "artifact_verification", "evidenceRefs": ["artifact_check"],
    }
    if continuation:
        result["continuation"] = admission(index)
    return result


def test_third_repair_requires_a_valid_host_budget_quote():
    value = repair_document(3)
    parsed = parse_implementation_repair_request(value)
    assert parsed == value
    parsed["continuation"]["usedModelCalls"] = 0
    assert value["continuation"]["usedModelCalls"] == 6
    with pytest.raises(ValueError):
        parse_implementation_repair_request(repair_document(3, continuation=False))


@pytest.mark.parametrize("change", [
    {"continuationVersion": True}, {"sessionId": "../foreign"}, {"extra": 1},
    {"limitModelCalls": True}, {"limitTotalTokens": 0}, {"usedModelCalls": -1},
    {"usedTotalTokens": True}, {"reservedTotalTokens": -1}, {"preserveModelCalls": 38},
    {"preserveTotalTokens": 499000}, {"projectedInputTokens": 500000},
    {"outputTokens": 0}, {"outputTokens": False},
    {"deadlineMonotonic": float("inf")}, {"deadlineMonotonic": True},
    {"admittedAtMonotonic": float("nan")}, {"admittedAtMonotonic": -1},
    {"admittedAtMonotonic": 1800},
])
def test_invalid_or_unfunded_quote_cannot_authorize_even_first_repair(change):
    value = repair_document()
    value["continuation"].update(change)
    with pytest.raises(ValueError):
        parse_implementation_repair_request(value)


def test_missing_quote_fields_and_legacy_extra_fields_fail_closed():
    value = repair_document()
    del value["continuation"]["reservedTotalTokens"]
    with pytest.raises(ValueError):
        parse_implementation_repair_request(value)
    legacy = repair_document(continuation=False)
    assert parse_implementation_repair_request(legacy) == legacy
    legacy["unknown"] = {}
    with pytest.raises(ValueError):
        parse_implementation_repair_request(legacy)


def test_admission_accepts_exact_remaining_budget_but_not_one_token_less():
    value = repair_document()
    value["continuation"].update(usedModelCalls=29, usedTotalTokens=409904)
    assert parse_implementation_repair_request(value) == value
    value["continuation"]["reservedTotalTokens"] = 1
    with pytest.raises(ValueError):
        parse_implementation_repair_request(value)


def continuation_trace(repairs=3):
    plan, outcome, transitions = repair_inputs(repairs=repairs)
    transitions = [replace(item, error_code=CONTINUATION)
                   if item.outcome == "repairing" else item for item in transitions]
    return project_scheduler_trace(plan, outcome, transitions)


def test_host_continuation_trace_accepts_three_revisions_without_reusing_gates():
    trace = continuation_trace()
    assert sum(item["outcome"] == "repairing" for item in trace["transitions"]) == 3
    assert trace["committed"] is True
    assert render_scheduler_trace(trace)
    with pytest.raises(ValueError):
        project_scheduler_trace(*repair_inputs(repairs=3))


@pytest.mark.parametrize("forgery", ["source", "identity", "no_verifier", "transition_overflow"])
def test_continuation_does_not_relax_trace_source_identity_or_size(forgery):
    if forgery == "transition_overflow":
        with pytest.raises(ValueError):
            continuation_trace(60)
        return
    trace = continuation_trace()
    items = trace["transitions"]
    boundary = next(i for i, item in enumerate(items) if item["outcome"] == "repairing")
    if forgery == "source":
        items[boundary]["artifactId"] = "artifact_unrelated"
    elif forgery == "identity":
        first = next(item for item in items if item["stage"] == "executor")
        items[boundary + 1]["agentRunId"] = first["agentRunId"]
    else:
        items[:] = [item for i, item in enumerate(items) if not (
            i > boundary and item["stage"] == "verifier")]
    previous = None
    for number, item in enumerate(items, 1):
        item.update(sequence=number, parentDecisionId=previous)
        previous = item["decisionId"]
    with pytest.raises(ValueError):
        render_scheduler_trace(trace)


def chain_documents(trace):
    """Minimal documents for the production revision-link validator seam."""
    documents = {}
    previous_execution = None
    previous_verification = None
    number = 0
    for position, item in enumerate(trace["transitions"]):
        if item["stage"] == "executor" and item["outcome"] == "completed":
            previous_execution = item
            documents[item["artifactId"]] = {
                "orderId": "order_" + item["assignmentId"],
                "changeSet": {"patchDigest": "sha256:" + str(number + 1) * 64},
            }
        if item["stage"] == "verifier" and item["outcome"] == "failed":
            previous_verification = item
            evidence_id = "evidence_" + item["assignmentId"]
            documents[item["artifactId"]] = {"status": "failed", "checks": [{
                "kind": "tests", "status": "failed", "exitCode": 1, "artifactRef": evidence_id,
            }]}
            documents[evidence_id] = {"status": "failed", "exitCode": 1, "failureReason": None}
        if item["outcome"] != "repairing":
            continue
        number += 1
        next_assignment = trace["transitions"][position + 1]["assignmentId"]
        execution = documents[previous_execution["artifactId"]]
        value = repair_document(number)
        value.update(
            workflowId=trace["workflowId"], taskId=trace["taskId"], assignmentId=next_assignment,
            sourceArtifactRef=item["artifactId"], previousExecutionArtifactRef=previous_execution["artifactId"],
            previousOrderRef=execution["orderId"],
            previousDiffArtifactRef=f"artifact_{previous_execution['assignmentId']}_{previous_execution['attemptId']}_frozen_diff",
            previousPatchDigest=execution["changeSet"]["patchDigest"],
            previousVerificationArtifactRef=previous_verification["artifactId"],
            evidenceRefs=["evidence_" + previous_verification["assignmentId"]],
        )
        documents["artifact_repair_" + next_assignment] = value
    return documents


def validate_chain(trace, documents):
    return scheduler_audit._validated_repair_inputs(trace, documents, request_artifact_id="artifact_request")


def test_audit_links_three_continuations_to_consumed_budget_and_exact_failed_patch():
    trace = continuation_trace()
    documents = chain_documents(trace)
    assert len(validate_chain(trace, documents)) == 3


@pytest.mark.parametrize("forgery", [
    "session", "call_limit", "token_limit", "later_deadline", "no_call_consumed", "tokens_decreased",
    "missing_quote", "legacy_quote", "previous_patch", "previous_order", "failed_evidence",
])
def test_audit_rejects_continuation_reset_or_rebound_failure(forgery):
    trace = continuation_trace()
    documents = chain_documents(trace)
    repairs = [value for value in documents.values() if value.get("resultType") == "ImplementationRepairRequest.v1"]
    second = repairs[1]
    quote = second["continuation"]
    if forgery == "session":
        quote["sessionId"] = "execsession_foreign"
    elif forgery == "call_limit":
        quote["limitModelCalls"] = 41
    elif forgery == "token_limit":
        quote["limitTotalTokens"] = 500001
    elif forgery == "later_deadline":
        quote["deadlineMonotonic"] = 1801.0
    elif forgery == "no_call_consumed":
        quote["usedModelCalls"] = repairs[0]["continuation"]["usedModelCalls"]
    elif forgery == "tokens_decreased":
        quote["usedTotalTokens"] = 9999
    elif forgery == "missing_quote":
        second.pop("continuation")
    elif forgery == "legacy_quote":
        next(item for item in trace["transitions"] if item["outcome"] == "repairing")["errorCode"] = "implementation_repair_requested"
    elif forgery == "previous_patch":
        second["previousPatchDigest"] = "sha256:" + "f" * 64
    elif forgery == "previous_order":
        second["previousOrderRef"] = repairs[0]["previousOrderRef"]
    else:
        documents[second["evidenceRefs"][0]]["failureReason"] = "timeout"
    with pytest.raises(SchedulerAuditValidationError):
        validate_chain(trace, documents)


def test_complete_public_audit_accepts_continuation_only_with_its_quote(tmp_path, monkeypatch):
    original = CentralScheduler.execute

    def legacy(self, profile, **kwargs):
        kwargs.pop("admit_implementation_repair", None)
        return original(self, profile, **kwargs)

    monkeypatch.setattr(CentralScheduler, "execute", legacy)
    result, _roles, _inputs, audits = _repair_run(tmp_path, monkeypatch, rejection="verifier")
    assert result.ok, result.output
    audit = audits[0]
    trace = deepcopy(audit.trace)
    next(item for item in trace["transitions"] if item["outcome"] == "repairing")["errorCode"] = CONTINUATION
    artifacts = []
    for artifact in audit.artifacts:
        document = json.loads(artifact.content)
        if document.get("resultType") == "ImplementationRepairRequest.v1":
            document["continuation"] = admission()
        # The durable stage receipt must describe the same host transition.
        if document.get("resultType") == "SchedulerStageReceipt.v1" and document.get("outcome") == "repairing":
            document["errorCode"] = CONTINUATION
        artifacts.append(replace(artifact, content=json.dumps(document, sort_keys=True, separators=(",", ":"))))
    projected = project_scheduler_audit(profile=audit.profile, plan=audit.plan, trace=trace,
        artifacts=tuple(artifacts), contract_mode="v2")
    assert projected.trace["committed"] is True
    malformed = tuple(replace(item, content=item.content.replace('"continuation":', '"unrecognized":'))
                      if '"ImplementationRepairRequest.v1"' in item.content else item for item in artifacts)
    with pytest.raises(SchedulerAuditValidationError):
        project_scheduler_audit(profile=audit.profile, plan=audit.plan, trace=trace,
            artifacts=malformed, contract_mode="v2")


def test_preparation_failure_before_quote_publication_is_terminal_not_authority(tmp_path, monkeypatch):
    from minicode.workflow_runner import _WorkflowRunSession

    original = CentralScheduler.execute

    def legacy(self, profile, **kwargs):
        kwargs.pop("admit_implementation_repair", None)
        return original(self, profile, **kwargs)

    def preparation_failed(_self, _assignment):
        raise ValueError("candidate is no longer available before preparation")

    monkeypatch.setattr(CentralScheduler, "execute", legacy)
    monkeypatch.setattr(_WorkflowRunSession, "_prepare_implementation_repair", preparation_failed)
    result, roles, _inputs, audits = _repair_run(tmp_path, monkeypatch, rejection="verifier")
    assert not result.ok and roles.count("executor") == 1
    assert (tmp_path / "allowed.txt").read_text() == "before"
    audit = audits[0]
    trace = deepcopy(audit.trace)
    next(item for item in trace["transitions"] if item["outcome"] == "repairing")["errorCode"] = CONTINUATION
    assert not any(json.loads(item.content).get("resultType") == "ImplementationRepairRequest.v1"
                   for item in audit.artifacts)
    projected = project_scheduler_audit(profile=audit.profile, plan=audit.plan, trace=trace,
        artifacts=audit.artifacts, contract_mode="v2")
    assert projected.trace["committed"] is False
