"""Explicit absent limits preserve identity, evidence and finite-budget authority."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from minicode.multi_agent_contracts import ContractValidationError, parse_assignment
from minicode.central_scheduler import CentralScheduler
from minicode.scheduler_audit import SchedulerAuditValidationError, project_scheduler_audit
from minicode.workflow_repair import validate_continuation_admission
from test_multi_agent_v2_contracts import _assignment
from test_workflow_continuation_contracts import (
    admission, chain_documents, continuation_trace, validate_chain,
)
from test_workflow_implementation_repair import _repair_run


@pytest.mark.parametrize("fields", [
    ("deadlineAt",), ("maxModelCalls",), ("maxInputTokens",), ("maxOutputTokens",),
    ("deadlineAt", "maxModelCalls", "maxInputTokens", "maxOutputTokens"),
])
def test_assignment_roundtrips_explicit_absent_limit_without_changing_scope(fields):
    raw = _assignment()
    for field in fields:
        raw["budget"][field] = None
    assert parse_assignment(raw).to_dict() == raw


@pytest.mark.parametrize("field", ["maxModelCalls", "maxInputTokens", "maxOutputTokens"])
@pytest.mark.parametrize("bad", [True, -1, 1.0, "unlimited"])
def test_explicit_absence_does_not_coerce_malformed_finite_limits(field, bad):
    raw = _assignment()
    raw["budget"][field] = bad
    with pytest.raises(ContractValidationError):
        parse_assignment(raw)


@pytest.mark.parametrize("bad", [True, -1, "", "2026-10-17", "2026-13-01T12:00:00Z"])
def test_assignment_deadline_remains_strict_when_present(bad):
    raw = _assignment()
    raw["budget"]["deadlineAt"] = bad
    with pytest.raises(ContractValidationError):
        parse_assignment(raw)


def test_unbounded_assignment_still_requires_every_limit_field_and_authorized_scope():
    raw = _assignment()
    raw["budget"] = dict.fromkeys(raw["budget"])
    missing = deepcopy(raw)
    del missing["budget"]["maxModelCalls"]
    with pytest.raises(ContractValidationError):
        parse_assignment(missing)
    raw["scope"]["writePaths"] = [".env"]
    with pytest.raises(ContractValidationError):
        parse_assignment(raw)


@pytest.mark.parametrize("absent", [
    ("limitModelCalls",), ("limitTotalTokens",), ("deadlineMonotonic",),
    ("limitModelCalls", "limitTotalTokens", "deadlineMonotonic"),
])
def test_continuation_absent_dimensions_still_record_actual_spending(absent):
    quote = admission()
    for field in absent:
        quote[field] = None
    assert validate_continuation_admission(quote) == quote


@pytest.mark.parametrize("change", [
    {"usedModelCalls": None}, {"usedTotalTokens": True}, {"reservedTotalTokens": -1},
    {"preserveModelCalls": None}, {"preserveTotalTokens": -1},
    {"projectedInputTokens": None}, {"outputTokens": None}, {"outputTokens": 0},
    {"admittedAtMonotonic": None}, {"admittedAtMonotonic": -1},
    {"admittedAtMonotonic": float("inf")}, {"admittedAtMonotonic": True},
])
def test_unbounded_quote_never_waives_real_usage_or_request_price(change):
    quote = admission()
    quote.update(limitModelCalls=None, limitTotalTokens=None, deadlineMonotonic=None)
    quote.update(change)
    with pytest.raises(ValueError):
        validate_continuation_admission(quote)


@pytest.mark.parametrize("field,bad", [
    ("limitModelCalls", True), ("limitModelCalls", 0), ("limitTotalTokens", -1),
    ("deadlineMonotonic", True), ("deadlineMonotonic", float("inf")),
    ("deadlineMonotonic", -1),
])
def test_limit_null_does_not_accept_other_invalid_limit_values(field, bad):
    quote = admission()
    quote[field] = bad
    with pytest.raises(ValueError):
        validate_continuation_admission(quote)


def _chain():
    trace = continuation_trace()
    documents = chain_documents(trace)
    repairs = [value for value in documents.values()
               if value.get("resultType") == "ImplementationRepairRequest.v1"]
    return trace, documents, repairs


@pytest.mark.parametrize("dimension,finite", [
    ("limitModelCalls", 39), ("limitTotalTokens", 499999), ("deadlineMonotonic", 1700.0),
])
def test_audit_allows_unbounded_or_stricter_continuation_without_reset(dimension, finite):
    trace, documents, repairs = _chain()
    for repair in repairs:
        repair["continuation"][dimension] = None
    assert len(validate_chain(trace, documents)) == 3
    for repair in repairs[1:]:
        repair["continuation"][dimension] = finite
    assert len(validate_chain(trace, documents)) == 3
    repairs[0]["continuation"][dimension] = finite + 1
    assert len(validate_chain(trace, documents)) == 3


@pytest.mark.parametrize("dimension", ["limitModelCalls", "limitTotalTokens", "deadlineMonotonic"])
def test_audit_rejects_finite_authority_becoming_unbounded(dimension):
    trace, documents, repairs = _chain()
    repairs[1]["continuation"][dimension] = None
    with pytest.raises(SchedulerAuditValidationError):
        validate_chain(trace, documents)


@pytest.mark.parametrize("tamper", ["session", "no_new_call", "usage_reset", "patch", "source"])
def test_unbounded_continuation_still_requires_same_owner_progress_and_failed_revision(tamper):
    trace, documents, repairs = _chain()
    for repair in repairs:
        repair["continuation"].update(
            limitModelCalls=None, limitTotalTokens=None, deadlineMonotonic=None,
        )
    second = repairs[1]
    if tamper == "session":
        second["continuation"]["sessionId"] = "execsession_foreign"
    elif tamper == "no_new_call":
        second["continuation"]["usedModelCalls"] = repairs[0]["continuation"]["usedModelCalls"]
    elif tamper == "usage_reset":
        second["continuation"]["usedTotalTokens"] = 0
    elif tamper == "patch":
        second["previousPatchDigest"] = "sha256:" + "f" * 64
    else:
        second["sourceArtifactRef"] = "artifact_foreign"
    with pytest.raises(SchedulerAuditValidationError):
        validate_chain(trace, documents)


def test_full_audit_accepts_explicit_null_assignment_and_quote_without_waiving_verification(
    tmp_path, monkeypatch,
):
    # Obtain an actual verified repair's artifact chain, then test only its
    # durable limit representation. This is not a live unbounded-loop proof.
    original = CentralScheduler.execute

    def legacy(self, profile, **kwargs):
        kwargs.pop("admit_implementation_repair", None)
        return original(self, profile, **kwargs)

    monkeypatch.setattr(CentralScheduler, "execute", legacy)
    result, _, _, audits = _repair_run(tmp_path, monkeypatch, rejection="verifier")
    assert result.ok, result.output
    audit = audits[0]
    trace = deepcopy(audit.trace)
    next(item for item in trace["transitions"] if item["outcome"] == "repairing")[
        "errorCode"
    ] = "implementation_continuation_requested"
    artifacts = []
    verification_ids = []
    for artifact in audit.artifacts:
        document = json.loads(artifact.content)
        if document.get("resultType") == "AssignmentArtifact.v1":
            document["assignment"]["budget"] = dict.fromkeys(document["assignment"]["budget"])
        if document.get("resultType") == "ImplementationRepairRequest.v1":
            document["continuation"] = admission()
            document["continuation"].update(
                limitModelCalls=None, limitTotalTokens=None, deadlineMonotonic=None,
            )
        if document.get("resultType") == "SchedulerStageReceipt.v1" and document.get("outcome") == "repairing":
            document["errorCode"] = "implementation_continuation_requested"
        if document.get("resultType") == "VerificationResult.v1":
            verification_ids.append(artifact.artifact_id)
        artifacts.append(replace(artifact, content=json.dumps(document, sort_keys=True, separators=(",", ":"))))
    assert any(json.loads(item.content).get("assignment", {}).get("budget", {}).get("deadlineAt", "missing")
               is None for item in artifacts)
    projected = project_scheduler_audit(
        profile=audit.profile, plan=audit.plan, trace=trace,
        artifacts=tuple(artifacts), contract_mode="v2",
    )
    assert projected.trace["committed"] is True
    assert len(verification_ids) == 2
    with pytest.raises(SchedulerAuditValidationError):
        project_scheduler_audit(
            profile=audit.profile, plan=audit.plan, trace=trace,
            artifacts=tuple(item for item in artifacts if item.artifact_id != verification_ids[-1]),
            contract_mode="v2",
        )
