"""Tiny native counter/read fixture, actual registry truncation, typed recovery.

No workflow snapshot, provider, project task or real candidate is used. The long
rejection is a generic host fixture, not a verdict copied from a real run.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from test_coverage_audit_recovery import _case
from minicode.agent_intelligence import ErrorClassifier, NudgeGenerator
from minicode.completion_coverage_inspection import CoverageInspection
from minicode.coverage_audit_recovery import HostCoverageAuditRecovery
from minicode.coverage_audit_state import (
    build_coverage_audit_state,
    host_record,
    record_document,
)
from minicode.coverage_audit_submission import CoverageDrafts
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.review_evidence_documents import canonical
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult


def rejection_case(tmp_path):
    case = _case(tmp_path)
    # The lightweight existing builder predates native resume's complete empty
    # anchor map. Set the generic host fixture before taking either snapshot.
    authority = record_document(case["authority"])
    authority["sourceAnchors"] = {row["id"]: [] for row in case["units"]}
    Path(case["authority"]["ref"]).write_text(canonical(authority))
    case["authority"] = host_record(case["authority"]["ref"], canonical(authority))
    case["old_pending"]["authorityRecord"] = case["authority"]
    case["manager"].messages = record_document(case["old_pending"]["messagesRecord"])[
        "messages"
    ]
    inspection = CoverageInspection(
        **case["inspection_kwargs"], record=case["old_pending"]["inspectionRecord"]
    )
    ready = {
        "status": "ready_for_evidence",
        "observations": [
            {
                "requirementId": case["units"][0]["id"],
                "status": "checked",
                "sourceEvidence": [
                    {"path": "counter.py", "startLine": 1, "endLine": 2}
                ],
                "expectedBehavior": "Increment by one.",
                "observedBehavior": "Read actual increment source.",
                "probeIds": [],
            }
        ],
        "findings": [],
    }
    assert case["execute"](
        "completion_coverage_inspection_submit",
        ready,
        inspection.registry(ToolRegistry([])),
    ).ok
    packet = {
        **case["bindings"],
        "authorityRef": case["authority"]["ref"],
        "evidenceDocumentsRef": case["documents"]["ref"],
        "requirements": case["units"],
        "proposal": {
            k: case["proposal"][k] for k in ("status", "requirements", "findings")
        },
        "behaviorContract": case["manifest"],
    }
    inspection.disclose([{"role": "user", "content": canonical(packet)}], lambda: None)
    old = build_coverage_audit_state(
        authority_record=case["authority"],
        documents_record=case["documents"],
        messages_record=case["retain"](
            "coverage-audit-messages",
            {
                "auditId": case["audit_id"],
                **case["bindings"],
                "messages": case["manager"].messages,
            },
        ),
        delivery_record=case["delivery"],
        inspection_record=inspection.record,
    )
    case["owner"]._pending_coverage_audit = old
    document = {**case["document"], "reviewState": case["owner"].state}
    continuation = HostImplementationContinuation.from_document(
        document, spec=case["spec"], messages=case["parent_messages"]
    )
    value = {"status": "approved", "requirements": [], "findings": []}
    started = case["retain"](
        "coverage-audit-tool-evidence",
        {
            "auditId": case["audit_id"],
            "toolName": "completion_coverage_audit_submit",
            "phase": "started",
            "input": value,
            "output": "",
            "isError": False,
            "invocationId": None,
        },
    )
    drafts = CoverageDrafts(case["bindings"], case["audit_id"])
    draft = drafts.retain(value, drafts.resolve(value))
    rejection = {
        "errorCode": "coverage_audit_invalid",
        "message": "Generic missing requirement evidence. " * 2400,
        "draftId": draft["draftId"],
        "draftStatus": "unaccepted",
        "missingDocumentIds": [],
    }
    draft_record = case["retain"](
        "coverage-audit-submission-draft", {**draft, "rejection": rejection}
    )
    full = canonical({**rejection, "draftRef": draft_record["ref"]})
    # The actual registry applies its normal structured-result-breaking legacy policy.
    tool = ToolDefinition(
        name="completion_coverage_audit_submit",
        description="Generic rejected draft fixture",
        input_schema={"type": "object"},
        validator=lambda v: v,
        run=lambda _v, _c: ToolResult(ok=False, output=full),
    )
    result = ToolRegistry([tool]).execute(tool.name, value, case["context"])
    assert not result.ok and len(result.output) == 30000
    with pytest.raises(json.JSONDecodeError):
        json.JSONDecoder().raw_decode(result.output)
    finished = case["retain"](
        "coverage-audit-tool-evidence",
        {
            "auditId": case["audit_id"],
            "toolName": tool.name,
            "phase": "finished",
            "input": None,
            "output": result.output,
            "isError": True,
            "invocationId": started["ref"],
        },
    )
    nudge = NudgeGenerator.generate(
        ErrorClassifier.classify(result.output, tool_name=tool.name), retry_count=0
    )
    call = {
        "role": "assistant_tool_call",
        "toolName": tool.name,
        "toolUseId": "generic-rejected-coverage",
        "input": value,
    }
    delivered = {
        "role": "tool_result",
        "toolName": tool.name,
        "toolUseId": call["toolUseId"],
        "isError": True,
        "content": result.output + "\n\n[System note: " + nudge + "]",
    }
    messages = [*case["manager"].messages, call, delivered]
    late = {
        "messagesRecord": case["retain"](
            "coverage-audit-messages",
            {"auditId": case["audit_id"], **case["bindings"], "messages": messages},
        ),
        "inspectionRecord": inspection.record,
        "deliveryRecord": case["retain"](
            "coverage-audit-delivery",
            {
                "auditId": case["audit_id"],
                **case["bindings"],
                "pairs": [{"call": call, "result": delivered}],
            },
        ),
    }
    originals = {
        "draftRecord": draft_record,
        "startedRecord": started,
        "finishedRecord": finished,
    }
    return {
        **case,
        "old_pending": old,
        "document": document,
        "continuation": continuation,
        "late": late,
        "originals": originals,
        "full": full,
        "visible": delivered["content"],
        "draft": draft,
    }


def recover(case):
    return HostCoverageAuditRecovery.from_records(
        continuation=case["continuation"],
        spec=case["spec"],
        messages_record=case["late"]["messagesRecord"],
        inspection_record=case["late"]["inspectionRecord"],
        delivery_record=case["late"]["deliveryRecord"],
        rejection_records=case["originals"],
        source_archive_digest="sha256:" + "c" * 64,
        persist=case["persist"],
    )


def test_same_owner_recovers_exact_visible_error_and_explicit_draft_without_read_credit(
    tmp_path,
):
    case = rejection_case(tmp_path)
    original = deepcopy(case["document"])
    before = {path: path.read_bytes() for _kind, path in case["records"]}
    with pytest.raises(json.JSONDecodeError):
        build_coverage_audit_state(
            authority_record=case["authority"],
            documents_record=case["documents"],
            messages_record=case["late"]["messagesRecord"],
            delivery_record=case["late"]["deliveryRecord"],
            inspection_record=case["late"]["inspectionRecord"],
        )
    recovery = recover(case)
    host = HostImplementationContinuation.for_coverage_recovery(
        case["document"],
        spec=case["spec"],
        messages=case["parent_messages"],
        recovery=recovery,
    )
    assert (
        host.document == case["document"] == original
        and host.attempt == case["continuation"].attempt
    )
    pending = host.restore_review(case["spec"]).state["pendingCoverageAudit"]
    messages = record_document(pending["messagesRecord"])["messages"]
    original_messages = record_document(case["late"]["messagesRecord"])["messages"]
    assert messages[:-1] == original_messages[:-1]
    result = messages[-1]
    metadata = result["_coverage_rejection_record"]
    assert {
        k: v for k, v in result.items() if k != "_coverage_rejection_record"
    } == original_messages[-1]
    assert result["content"] == case["visible"] and result["isError"] is True
    assert record_document(metadata)["nativeOutput"] == case["full"]
    assert record_document(metadata)["displayOutput"] != case["full"]
    assert pending["inspectionRecord"] == case["old_pending"]["inspectionRecord"]
    assert pending["documentsRecord"] == case["old_pending"]["documentsRecord"]
    assert record_document(pending["deliveryRecord"])["pairs"][-1]["result"] == result
    drafts = CoverageDrafts(case["bindings"], case["audit_id"])
    drafts.restore_rejection(
        messages[-2]["input"],
        result["content"],
        rejection_record=metadata,
        tool_use_id=messages[-2]["toolUseId"],
    )
    assert drafts.latest == case["draft"]["draftId"]
    corrected = drafts.resolve(
        {
            "draftId": drafts.latest,
            "updates": [{"path": "/status", "value": "inconclusive"}],
        }
    )
    assert corrected == {**case["draft"]["submission"], "status": "inconclusive"}
    assert drafts.get(drafts.latest)["status"] == "unaccepted"
    receipt = record_document(recovery.receipt_record)
    assert (
        receipt["addedErrorDeliveries"] == []
        and receipt["fullRejectionPreviouslyDelivered"] is False
    )
    assert (
        receipt["newSourceReadCredit"]
        is receipt["newChallengeCredit"]
        is receipt["approvalGranted"]
        is False
    )
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert len(canonical(receipt)) < 12000
    # Re-derive from pinned artifacts with read-only persistence.
    saved = {
        kind: path
        for kind, path in case["records"]
        if kind.startswith("coverage-audit-recovery-")
    }

    def readonly(kind, value):
        assert json.loads(saved[kind].read_bytes()) == value
        return str(saved[kind])

    case["persist"] = readonly
    assert recover(case).pending_state == pending


def test_restored_native_auditor_receives_original_display_and_explicitly_corrects_draft(
    tmp_path, monkeypatch
):
    import io
    from minicode.agent_budget import AgentTurnBudget
    from minicode.completion_coverage_audit import audit_completion_coverage
    from minicode.openai_adapter import OpenAIModelAdapter
    from minicode.tooling import ToolContext

    case = rejection_case(tmp_path)
    recovery = recover(case)
    pending = (
        HostImplementationContinuation.for_coverage_recovery(
            case["document"],
            spec=case["spec"],
            messages=case["parent_messages"],
            recovery=recovery,
        )
        .restore_review(case["spec"])
        .state["pendingCoverageAudit"]
    )
    runtime = {
        "model": "gpt-test",
        "openaiBaseUrl": "https://openai.invalid/v1",
        "openaiApiKey": "test",
        "modelMaxRetries": 0,
        "maxOutputTokens": 2048,
    }
    requests = []

    class Response(io.BytesIO):
        status = 200

    def transport(request, *, timeout):
        body = json.loads(request.data)
        requests.append(body)
        assert len(requests) == 1
        assert any(
            row.get("role") == "tool" and row.get("content") == case["visible"]
            for row in body["messages"]
        )
        assert "_coverage_rejection_record" not in json.dumps(body)
        patch = {
            "draftId": case["draft"]["draftId"],
            "updates": [
                {"path": "/status", "value": "inconclusive"},
                {
                    "path": "/findings",
                    "value": ["Independent requirement evidence remains incomplete."],
                },
            ],
        }
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "explicit-correction",
                    "type": "function",
                    "function": {
                        "name": "completion_coverage_audit_submit",
                        "arguments": json.dumps(patch),
                    },
                }
            ],
        }
        return Response(
            json.dumps(
                {
                    "choices": [{"message": message, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30},
                }
            ).encode()
        )

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    host = ToolContext(
        cwd=str(tmp_path),
        permissions=case["permissions"],
        _runtime=runtime,
        _agent_budget=AgentTurnBudget(),
    )
    adapter = OpenAIModelAdapter(runtime, case["tools"])
    decision = audit_completion_coverage(
        objective=case["objective"],
        requirements=case["units"],
        proposal=case["proposal"],
        candidate_digest=case["candidate"],
        model=adapter,
        tools=case["tools"],
        cwd=str(tmp_path),
        permissions=case["permissions"],
        host=host,
        event_sink=None,
        persist=case["persist"],
        checkpoint=lambda: None,
        probe_evidence=lambda _pid: None,
        source_anchors=case["anchors"],
        continuation_state=pending,
    )
    assert decision["status"] == "inconclusive" and len(requests) == 1
    assert decision["findings"] == [
        "Independent requirement evidence remains incomplete."
    ]
    assert (
        record_document(decision["inspectionRecord"])["reads"]
        == record_document(pending["inspectionRecord"])["reads"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "prefix",
        "success",
        "binding",
        "draft_identity",
        "draft_input",
        "native_diagnostic",
        "finished_output",
        "invocation",
        "visible_suffix",
        "extra_pair",
        "inspection",
        "directory",
    ],
)
def test_rejection_recovery_refuses_tampered_or_unrelated_originals(tmp_path, mutation):
    case = rejection_case(tmp_path)
    key, records = "messagesRecord", case["late"]
    value = record_document(records[key])
    if mutation == "prefix":
        value["messages"][0]["content"] += " altered"
    elif mutation == "success":
        value["messages"][-1]["isError"] = False
    elif mutation == "binding":
        value["candidateDigest"] = "sha256:" + "f" * 64
    elif mutation == "visible_suffix":
        value["messages"][-1]["content"] += " arbitrary injected suffix"
        pair = record_document(case["late"]["deliveryRecord"])
        pair["pairs"][-1]["result"]["content"] = value["messages"][-1]["content"]
        case["late"]["deliveryRecord"] = host_record(
            case["late"]["deliveryRecord"]["ref"], canonical(pair)
        )
    elif mutation == "extra_pair":
        value["messages"] += deepcopy(value["messages"][-2:])
    elif mutation in {"draft_identity", "draft_input", "native_diagnostic"}:
        key, records = "draftRecord", case["originals"]
        value = record_document(records[key])
        if mutation == "draft_identity":
            value["draftId"] = "different"
        elif mutation == "draft_input":
            value["originalInput"]["findings"] = ["Changed native request."]
        else:
            value["rejection"]["message"] += " changed"
    elif mutation in {"finished_output", "invocation"}:
        key, records = "finishedRecord", case["originals"]
        value = record_document(records[key])
        value["output" if mutation == "finished_output" else "invocationId"] += (
            " changed"
        )
    elif mutation == "inspection":
        key = "inspectionRecord"
        value = record_document(records[key])
        value["phase"] = "inspection"
    elif mutation == "directory":
        case["originals"]["draftRecord"]["ref"] = str(
            tmp_path / "foreign/00089-coverage-audit-submission-draft.json"
        )
    if mutation != "directory":
        records[key] = host_record(records[key]["ref"], canonical(value))
    saved = len(case["records"])
    with pytest.raises((ValueError, TypeError)):
        recover(case)
    assert len(case["records"]) == saved


def archive_case(tmp_path):
    from scripts import run_requirement_completion_checkpoint as runner

    native = tmp_path / "native"
    native.mkdir()
    case = rejection_case(native)
    source = tmp_path / "ancestor"
    root = source / "private" / runner.TRIAL_ID
    root.mkdir(parents=True)
    runner.checkpoint_writer(root)(case["document"], case["parent_messages"])
    runner.private_json(source / "manifest.json", {"crossProcessContinuation": False})
    archived = (
        root
        / "completion-review"
        / ("review_" + hashlib.sha256(str(native.resolve()).encode()).hexdigest()[:24])
    )
    archived.mkdir(parents=True)
    for _kind, path in case["records"]:
        shutil.copyfile(path, archived / path.name)
    runner.private_json(
        root / "completion-review-archive.json",
        {
            "archiveVersion": 1,
            "archiveComplete": True,
            "errors": [],
            "files": [
                {
                    "reviewId": archived.name,
                    "file": str(path.relative_to(root)),
                    "sha256": runner.sha_file(path),
                    "bytes": path.stat().st_size,
                }
                for path in sorted(archived.iterdir())
            ],
        },
    )
    return {**case, "source": source, "source_root": root, "archived": archived}


def test_archive_bridge_pins_native_records_and_readonly_restores_typed_rejection(
    tmp_path,
):
    from scripts import run_requirement_completion_checkpoint as runner

    case = archive_case(tmp_path)
    original = {p: p.read_bytes() for p in case["source"].rglob("*") if p.is_file()}
    output = tmp_path / "prepared"
    output.mkdir()
    runner.private_json(output / "private/host-continuation.json", case["document"])
    contract = runner.prepare_coverage_recovery(
        output,
        case["source"],
        predecessor=case["source"],
        document=case["document"],
        spec=case["spec"],
        messages=case["parent_messages"],
    )
    runner.private_json(
        output / "checkpoint.json",
        {
            "predecessorDirectory": str(case["source"]),
            "coverageAuditRecovery": contract,
        },
    )
    host = runner.restored_continuation(output, case["spec"], case["parent_messages"])
    assert host.document == case["document"]
    pending = host.restore_review(case["spec"]).state["pendingCoverageAudit"]
    assert (
        record_document(pending["messagesRecord"])["messages"][-1]["content"]
        == case["visible"]
    )
    descriptor = json.loads((output / contract["file"]).read_bytes())
    assert set(descriptor["rejectionRecords"]) == {
        "draftRecord",
        "startedRecord",
        "finishedRecord",
    }
    assert set(descriptor["generatedRecords"]) == set(runner._REJECTION_RECOVERY_FILES)
    assert all(p.read_bytes() == raw for p, raw in original.items())
    record = output / "private/coverage-recovery-rejection.json"
    record.write_bytes(record.read_bytes() + b" ")
    with pytest.raises(ValueError, match="derived record changed"):
        runner.restored_continuation(output, case["spec"], case["parent_messages"])


@pytest.mark.parametrize(
    "mutation", [None, "not_explicit", "wrong_frames", "not_completed", "dirty_scope"]
)
def test_only_exact_authenticated_checkpoint_error_can_resume_after_model_calls(
    tmp_path, monkeypatch, mutation
):
    from scripts import run_requirement_completion_checkpoint as runner

    case = archive_case(tmp_path)
    root, source, document = case["source_root"], case["source"], case["document"]
    execution = {"errorClass": "JSONDecodeError"}
    row = {
        "id": runner.TRIAL_ID,
        "success": False,
        "errorClass": "JSONDecodeError",
        "processCleanup": {"cleanupComplete": True},
        "judgement": {"cleanupComplete": True},
        "scopePassed": mutation != "dirty_scope",
        "unauthorizedChanges": [],
        "verificationInducedChanges": [],
    }
    runner.private_json(
        source / "results.json",
        {
            "manifestSha256": runner.sha_file(source / "manifest.json"),
            "expectedTrials": 1,
            "trials": [row],
        },
    )
    frames = [
        {"file": "/generic/minicode/" + name, "name": function}
        for name, function in (
            ("completion_coverage_audit.py", "checkpoint_context"),
            ("completion_coverage_audit.py", "messages_checkpoint"),
            ("coverage_audit_state.py", "build_coverage_audit_state"),
            ("coverage_audit_state.py", "validate_coverage_audit_state"),
            ("coverage_audit_state.py", "restore_evidence_documents"),
            ("coverage_audit_submission.py", "restore_rejection"),
        )
    ]
    if mutation == "wrong_frames":
        frames[-1]["name"] = "unrelated_json_parse"
    runner.private_json(
        root / "error.json", {"class": "JSONDecodeError", "frames": frames}
    )
    event = {
        "runId": "run_generic",
        "sequence": 1,
        "type": "implementation.completion_review_started",
        "payload": {
            "executorId": document["executorId"],
            "attempt": document["attempt"],
            "patchDigest": document["candidateDigest"],
        },
    }
    path = root / "journal/dashboard/workspaces/generic/runs/run_generic/events.ndjson"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(event) + "\n")
    monkeypatch.setattr(
        runner,
        "last_transport_metadata",
        lambda _root: {
            "event": "failed" if mutation == "not_completed" else "completed",
            "call": 1,
        },
    )
    kwargs = dict(
        recover_rejected_coverage=mutation != "not_explicit", spec=case["spec"]
    )

    def classify():
        return runner.terminal_evidence(
            source,
            document,
            execution,
            case["parent_messages"],
            {
                key: document[key]
                for key in ("executorId", "attempt", "candidateDigest")
            },
            **kwargs,
        )

    if mutation is not None:
        with pytest.raises(ValueError):
            classify()
    else:
        events, evidence = classify()
        assert events and evidence["classification"] == "coverage-checkpoint-failed"
        assert evidence["recoveredRejectedCallId"] == "generic-rejected-coverage"
        assert (
            evidence["lastPhysicalCall"]["call"] == 1
            and evidence["hiddenJudgeUsed"] is False
        )
