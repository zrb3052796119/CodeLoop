"""Generic native draft/read preservation after host clears pending on transport stop."""

from copy import deepcopy
from pathlib import Path
import json

import pytest

from test_coverage_rejection_recovery import (
    rejection_case,
    recover as rejection_recover,
)
from minicode.coverage_audit_recovery import HostCoverageAuditRecovery
from minicode.coverage_audit_state import (
    build_coverage_audit_state,
    record_document,
    restore_evidence_documents,
    host_record,
)
from minicode.coverage_audit_submission import (
    CoverageDrafts,
    rejection_delivery_document,
    _native_json,
    rejection_view,
)
from minicode.completion_evidence_view import EvidenceViews
from minicode.completion_coverage_audit import _digest as audit_digest
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.review_evidence_documents import EvidenceDocuments, canonical


def interrupted_case(tmp_path):
    case = rejection_case(tmp_path)
    initial = rejection_recover(case).pending_state
    store = EvidenceDocuments(candidate_digest=case["candidate"], page_size=100)
    reference = store.add(
        {"generic inventory evidence": "retain public counter diagnostics " * 400},
        "generic",
        factor=False,
    )
    document_path = Path(initial["documentsRecord"]["ref"])
    document_path.write_text(
        canonical({**case["bindings"], "documents": store.snapshot()})
    )
    initial = build_coverage_audit_state(
        authority_record=initial["authorityRecord"],
        documents_record=host_record(str(document_path), document_path.read_bytes()),
        messages_record=initial["messagesRecord"],
        delivery_record=initial["deliveryRecord"],
        inspection_record=initial["inspectionRecord"],
    )
    old_messages = record_document(initial["messagesRecord"])["messages"]
    old_pairs = record_document(initial["deliveryRecord"])["pairs"]
    messages, pairs = deepcopy(old_messages), deepcopy(old_pairs)
    # These are actual evidence-document page responses, not fabricated credit.
    output = None
    for i in range(10):
        args = {"documentId": reference} if i == 0 else {"cursor": output["nextCursor"]}
        output = store.read(args)
        call = {
            "role": "assistant_tool_call",
            "toolName": "review_evidence_read",
            "toolUseId": "page-" + str(i),
            "input": args,
        }
        result = {
            "role": "tool_result",
            "toolName": call["toolName"],
            "toolUseId": call["toolUseId"],
            "isError": False,
            "content": canonical(output),
        }
        messages.extend((call, result))
        pairs.append({"call": call, "result": result})
    drafts = CoverageDrafts(case["bindings"], case["audit_id"])
    for pair in old_pairs:
        drafts.restore_rejection(
            pair["call"]["input"],
            pair["result"]["content"],
            rejection_record=pair["result"].get("_coverage_rejection_record"),
            tool_use_id=pair["call"]["toolUseId"],
        )
    for i in range(2):
        args = {
            "draftId": drafts.latest,
            "updates": [
                {"path": "/findings", "value": ["Unresolved public evidence " + str(i)]}
            ],
        }
        draft = drafts.retain(args, drafts.resolve(args))
        output = _native_json(
            {
                "errorCode": "coverage_audit_invalid",
                "message": "Requirement evidence remains incomplete.",
                "draftId": draft["draftId"],
                "draftStatus": "unaccepted",
            }
        )
        display = _native_json(
            rejection_view(
                json.loads(output), EvidenceViews(candidate_digest=case["candidate"])
            )
        )
        call = {
            "role": "assistant_tool_call",
            "toolName": "completion_coverage_audit_submit",
            "toolUseId": "draft-" + str(i),
            "input": args,
        }
        result = {
            "role": "tool_result",
            "toolName": call["toolName"],
            "toolUseId": call["toolUseId"],
            "isError": True,
            "content": display,
        }
        result["_coverage_rejection_record"] = case["retain"](
            "coverage-audit-rejection-delivery",
            rejection_delivery_document(
                bindings=case["bindings"],
                audit_id=case["audit_id"],
                original_input=args,
                tool_use_id=call["toolUseId"],
                native_output=output,
                display_output=display,
                delivered_output=display,
                projection="native_view_v1",
            ),
        )
        messages.extend((call, result))
        pairs.append({"call": call, "result": result})
    messages.extend(
        [
            {
                "role": "system",
                "content": "Generic provider request failed.",
                "_model_transport_failure": True,
            }
            for _ in range(4)
        ]
    )
    messages.append(
        {
            "role": "assistant",
            "content": "Incomplete provider response retained.",
            "_partial": True,
        }
    )
    late = {
        "messagesRecord": case["retain"](
            "coverage-audit-messages",
            {"auditId": case["audit_id"], **case["bindings"], "messages": messages},
        ),
        "deliveryRecord": case["retain"](
            "coverage-audit-delivery",
            {"auditId": case["audit_id"], **case["bindings"], "pairs": pairs},
        ),
        "inspectionRecord": initial["inspectionRecord"],
    }
    copied = case["retain"](
        "coverage-audit-authority", record_document(initial["authorityRecord"])
    )
    decision = {
        "status": "inconclusive",
        "requirements": [],
        "findings": ["The auditor returned without a valid submission."],
        "inspectionRecord": initial["inspectionRecord"],
        **case["bindings"],
        "authorityRef": copied["ref"],
        "authorityProvenance": {
            "originalAuthorityRef": initial["authorityRecord"]["ref"],
            "originalAuthorityDigest": initial["authorityRecord"]["sha256"],
            "auditId": case["audit_id"],
        },
    }
    decision_record = case["retain"]("coverage-audit-decision", decision)
    review = {
        "status": "inconclusive",
        "requirements": [],
        "findings": ["Interrupted"],
        "patchDigest": case["candidate"],
        "coverageAudit": {
            **decision,
            "evidenceRef": decision_record["ref"],
            "evidenceDigest": audit_digest(decision),
        },
    }
    document = deepcopy(case["document"])
    document["attempt"] += 1
    document["reviewState"].update(
        pendingCoverageAudit=None, evidenceDirectory=str(tmp_path), reviews=[review]
    )
    continuation = HostImplementationContinuation.from_document(
        document, spec=case["spec"], messages=case["parent_messages"]
    )
    originals = {
        "initialState": initial,
        "decisionRecord": decision_record,
        "decisionAuthorityRecord": copied,
    }
    return {
        **case,
        "document": document,
        "continuation": continuation,
        "initial": initial,
        "late": late,
        "interruption": originals,
        "latest_draft": drafts.latest,
        "store": store,
        "reference": reference,
    }


def recover(case, **kwargs):
    return HostCoverageAuditRecovery.from_records(
        continuation=case["continuation"],
        spec=case["spec"],
        messages_record=case["late"]["messagesRecord"],
        inspection_record=case["late"]["inspectionRecord"],
        delivery_record=case["late"]["deliveryRecord"],
        source_archive_digest="sha256:" + "c" * 64,
        persist=case["persist"],
        interruption_records=case["interruption"],
        **kwargs,
    )


def test_recovery_preserves_exact_terminal_history_pages_and_two_unaccepted_edits(
    tmp_path,
):
    case = interrupted_case(tmp_path)
    original = deepcopy(case["document"])
    native_bytes = {p: p.read_bytes() for _k, p in case["records"]}
    recovery = recover(case)
    host = HostImplementationContinuation.for_coverage_recovery(
        case["document"],
        spec=case["spec"],
        messages=case["parent_messages"],
        recovery=recovery,
    )
    restored = host.restore_review(case["spec"]).state
    pending = restored["pendingCoverageAudit"]
    assert host.document == original == case["document"]
    assert host.executor_id == case["continuation"].executor_id and host.attempt == 3
    assert restored["reviews"] == original["reviewState"]["reviews"]
    assert pending["messagesRecord"] == case["late"]["messagesRecord"]
    assert pending["deliveryRecord"] == case["late"]["deliveryRecord"]
    assert pending["inspectionRecord"] == case["initial"]["inspectionRecord"]
    actual = restore_evidence_documents(pending)
    assert actual.snapshot() == case["store"].snapshot()
    drafts = CoverageDrafts(case["bindings"], case["audit_id"])
    for pair in record_document(pending["deliveryRecord"])["pairs"]:
        if pair["call"]["toolName"] == "completion_coverage_audit_submit":
            drafts.restore_rejection(
                pair["call"]["input"],
                pair["result"]["content"],
                rejection_record=pair["result"].get("_coverage_rejection_record"),
                tool_use_id=pair["call"]["toolUseId"],
            )
    assert (
        drafts.latest == case["latest_draft"]
        and drafts.get(drafts.latest)["status"] == "unaccepted"
    )
    receipt = record_document(recovery.receipt_record)
    assert receipt["recoveredMessages"] - receipt["initialMessages"] == 29
    assert receipt["recoveredDeliveries"] - receipt["initialDeliveries"] == 12
    assert (
        receipt["newSourceReadCredit"]
        is receipt["newChallengeCredit"]
        is receipt["approvalGranted"]
        is False
    )
    assert all(p.read_bytes() == raw for p, raw in native_bytes.items())
    recovery.validate_for(original, spec=case["spec"])


@pytest.mark.parametrize(
    "change",
    [
        "candidate",
        "owner",
        "accepted",
        "prefix",
        "result",
        "inspection",
        "terminal",
        "missing_transport",
    ],
)
def test_recovery_rejects_changed_authority_or_invented_delivery(tmp_path, change):
    case = interrupted_case(tmp_path)
    if change in {"candidate", "owner"}:
        # A typed overlay remains bound to its exact owner/document on use.
        recovery = recover(case)
        altered = deepcopy(case["document"])
        altered["candidateDigest" if change == "candidate" else "executorId"] = (
            "sha256:" + "d" * 64
            if change == "candidate"
            else "implementation_" + "d" * 32
        )
        with pytest.raises(ValueError):
            recovery.validate_for(altered, spec=case["spec"])
        return
    if change == "inspection":
        value = record_document(case["late"]["inspectionRecord"])
        value["phase"] = "inspection"
        case["late"]["inspectionRecord"] = case["retain"]("coverage-inspection", value)
    elif change == "terminal":
        value = record_document(case["interruption"]["decisionRecord"])
        value["status"] = "approved"
        case["interruption"]["decisionRecord"] = case["retain"](
            "coverage-audit-decision", value
        )
    else:
        messages = record_document(case["late"]["messagesRecord"])
        pairs = record_document(case["late"]["deliveryRecord"])
        if change == "prefix":
            messages["messages"][0]["content"] = "Changed context"
        elif change == "missing_transport":
            messages["messages"] = [
                m for m in messages["messages"] if not m.get("_model_transport_failure")
            ]
        elif change == "accepted":
            pairs["pairs"][-1]["result"]["isError"] = False
            messages["messages"][-6]["isError"] = False
        else:
            # Both record hashes are valid, but the page bytes are invented.
            pair = pairs["pairs"][1]
            result = json.loads(pair["result"]["content"])
            result["text"] = "invented page"
            pair["result"]["content"] = canonical(result)
            for m in messages["messages"]:
                if (
                    m.get("toolUseId") == pair["call"]["toolUseId"]
                    and m["role"] == "tool_result"
                ):
                    m["content"] = pair["result"]["content"]
        case["late"]["messagesRecord"] = case["retain"](
            "coverage-audit-messages", messages
        )
        case["late"]["deliveryRecord"] = case["retain"](
            "coverage-audit-delivery", pairs
        )
    with pytest.raises(ValueError):
        recover(case)


def archived_case(tmp_path):
    import hashlib
    from scripts import run_requirement_completion_checkpoint as runner

    case_dir = tmp_path / "native"
    case_dir.mkdir()
    case = interrupted_case(case_dir)
    source = tmp_path / "source"
    root = source / "private" / runner.TRIAL_ID
    root.mkdir(parents=True)
    archive = (
        root
        / "completion-review"
        / (
            "review_"
            + hashlib.sha256(str(case_dir.resolve()).encode()).hexdigest()[:24]
        )
    )
    archive.mkdir(parents=True)
    initial = case["initial"]
    # Only this run's actual initial/latest native snapshots belong to its archive.
    selected = {
        Path(value["ref"]).name: value["rawJson"].encode()
        for key, value in initial.items()
        if key.endswith("Record") and key not in {"messagesRecord", "deliveryRecord"}
    }
    for name, key in (
        ("00000-coverage-audit-messages.json", "messagesRecord"),
        ("00001-coverage-audit-delivery.json", "deliveryRecord"),
    ):
        selected[name] = initial[key]["rawJson"].encode()
    for value in [
        *case["late"].values(),
        case["interruption"]["decisionRecord"],
        case["interruption"]["decisionAuthorityRecord"],
    ]:
        selected[Path(value["ref"]).name] = value["rawJson"].encode()
    for name, raw in selected.items():
        (archive / name).write_bytes(raw)
    archive_manifest = {
        "archiveComplete": True,
        "errors": [],
        "files": [
            {
                "file": str(p.relative_to(root)),
                "sha256": runner.sha_file(p),
                "bytes": p.stat().st_size,
            }
            for p in sorted(archive.iterdir())
        ],
    }

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical(value))

    write(root / "completion-review-archive.json", archive_manifest)
    write(root / "implementation-checkpoint.json", case["document"])
    write(root / "implementation-checkpoint-messages.json", case["parent_messages"])
    write(
        root / "implementation-checkpoint-receipt.json",
        {
            "available": True,
            "source": "production_parent_finally",
            **{
                k: case["document"][k]
                for k in ("executorId", "candidateDigest", "attempt")
            },
            "documentSha256": runner.sha_file(root / "implementation-checkpoint.json"),
            "messagesSha256": runner.sha_file(
                root / "implementation-checkpoint-messages.json"
            ),
        },
    )
    inherited = {
        "coverageRecoveryVersion": 2,
        "reason": "truncated_rejection_checkpoint_lag",
        **{
            k: case["document"][k]
            for k in ("executorId", "candidateDigest", "specDigest")
        },
        "attempt": case["document"]["attempt"] - 1,
        "auditId": case["audit_id"],
        "oldRecords": {
            k: {
                "ref": v["ref"],
                "sha256": v["sha256"],
                "bytes": len(v["rawJson"].encode()),
            }
            for k, v in initial.items()
            if k.endswith("Record")
        },
    }
    write(source / "private/coverage-recovery-receipt.json", inherited)
    descriptor = {
        "generatedRecords": {
            "coverage-audit-recovery-receipt": {
                "file": "private/coverage-recovery-receipt.json",
                "sha256": runner.sha_file(
                    source / "private/coverage-recovery-receipt.json"
                ),
            }
        }
    }
    write(source / "private/coverage-recovery.json", descriptor)
    write(
        source / "checkpoint.json",
        {
            "identity": {
                "executorId": case["document"]["executorId"],
                "candidateDigest": case["document"]["candidateDigest"],
                "attempt": inherited["attempt"],
            },
            "coverageAuditRecovery": {
                "kind": "typed_same_audit_archive_recovery",
                "file": "private/coverage-recovery.json",
                "sha256": runner.sha_file(source / "private/coverage-recovery.json"),
            },
        },
    )
    write(
        source / "manifest.json",
        {
            "crossProcessContinuation": False,
            "continuationCheckpointSha256": runner.sha_file(source / "checkpoint.json"),
        },
    )
    return case, runner, source, archive


def test_harness_prepares_and_restores_explicit_overlay_from_authenticated_archive(
    tmp_path,
):
    case, runner, source, archive = archived_case(tmp_path)
    output = tmp_path / "prepared"
    (output / "private").mkdir(parents=True)
    record = runner.prepare_coverage_recovery(
        output,
        source,
        predecessor=source,
        document=case["document"],
        spec=case["spec"],
        messages=case["parent_messages"],
    )
    (output / "checkpoint.json").write_text(
        canonical(
            {"coverageAuditRecovery": record, "predecessorDirectory": str(source)}
        )
    )
    recovered = runner.saved_coverage_recovery(
        output,
        spec=case["spec"],
        messages=case["parent_messages"],
        document=case["document"],
    )
    assert recovered.pending_state["messagesRecord"] == case["late"]["messagesRecord"]
    assert recovered.pending_state["deliveryRecord"] == case["late"]["deliveryRecord"]
    assert record_document(recovered.receipt_record)["historyRewritten"] is False
    assert list((output / "private").iterdir()) == [
        output / "private/coverage-recovery-receipt.json",
        output / "private/coverage-recovery.json",
    ]
    # Changing either the derived receipt or an authenticated original is refused.
    saved = output / "private/coverage-recovery-receipt.json"
    raw = saved.read_bytes()
    saved.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="derived record changed"):
        runner.saved_coverage_recovery(
            output,
            spec=case["spec"],
            messages=case["parent_messages"],
            document=case["document"],
        )
    saved.write_bytes(raw)
    original = archive / Path(case["late"]["messagesRecord"]["ref"]).name
    original.write_bytes(original.read_bytes() + b" ")
    with pytest.raises(ValueError, match="archive hash mismatch"):
        runner.coverage_recovery_source(
            source, predecessor=source, document=case["document"]
        )


def test_harness_cannot_rewind_to_an_ancestor_after_current_terminal(tmp_path):
    case, runner, source, _archive = archived_case(tmp_path)
    from scripts.coverage_interruption_bridge import interrupted_coverage_source

    with pytest.raises(ValueError, match="actual terminal predecessor"):
        interrupted_coverage_source(
            runner, source, tmp_path / "different", case["document"], [source]
        )


def test_freeze_includes_recovery_bridge_and_validate_binds_its_actual_bytes(
    tmp_path, monkeypatch
):
    """Exercise real freeze/hash/validate while isolating unrelated host loading."""
    import shutil
    import zipfile
    from types import SimpleNamespace
    from scripts import run_requirement_completion_checkpoint as runner

    name = "coverage_interruption_bridge.py"
    assert name in runner.HARNESS_FILES
    code = tmp_path / "code"
    (code / "scripts").mkdir(parents=True)
    (code / "minicode").mkdir()
    for file in runner.HARNESS_FILES:
        shutil.copyfile(runner.ROOT / "scripts" / file, code / "scripts" / file)
    (code / "minicode" / "generic.py").write_text("GENERIC = True\n")
    monkeypatch.setattr(runner, "ROOT", code)
    output = tmp_path / "frozen"
    output.mkdir()
    for file in ("checkpoint.json", "tasks.json"):
        (output / file).write_text("{}")
    (output / "analysis-plan.md").write_text("Generic byte-freezing test.")
    checkpoint = {
        "coverageAuditRecovery": {"kind": "test-host-loading"},
        "transportChange": {},
        "predecessorDirectory": str(tmp_path / "prior"),
        "originalModelPolicy": {},
    }
    inputs = {
        "case": {},
        "manifest": {"taskSha256": runner.sha_file(output / "tasks.json")},
    }
    monkeypatch.setattr(
        runner, "load_checkpoint", lambda *_a, **_kw: (checkpoint, inputs, [])
    )
    monkeypatch.setattr(runner, "continuation_capabilities", lambda: {})
    monkeypatch.setattr(runner, "implementation_spec", lambda _case: None)
    monkeypatch.setattr(
        runner,
        "restored_continuation",
        lambda *_a: SimpleNamespace(
            restore_review=lambda _spec: SimpleNamespace(
                state={"genericHostState": True}
            )
        ),
    )
    original = (code / "scripts" / name).read_bytes()
    before = runner.harness_sha()
    runner.freeze(output)
    with zipfile.ZipFile(output / "frozen-code.zip") as archive:
        assert archive.namelist().count("scripts/" + name) == 1
        assert archive.read("scripts/" + name) == original
    assert runner.validate(output)["harnessSha256"] == before
    (code / "scripts" / name).write_bytes(original + b"\n# byte-change control\n")
    assert runner.harness_sha() != before
    with pytest.raises(ValueError, match="frozen continuation contract changed"):
        runner.validate(output)
