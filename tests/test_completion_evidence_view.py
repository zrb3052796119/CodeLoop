"""Display projections retain real pytest evidence without sending whole traces."""

from copy import deepcopy
import json
import sys

import pytest

from minicode.completion_evidence_view import EvidenceViews
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.permissions import PermissionManager
from minicode.pytest_execution_evidence import validate_execution_report
from minicode.review_evidence_documents import canonical, content_digest
from minicode.review_probe_execution import run_isolated_review_probe
from minicode.reviewer_verification import ReviewerProbe, ReviewerProbeLedger
from minicode.workflow_workspace import WorkflowWorkspaceTransaction


@pytest.fixture
def actual_receipt(tmp_path, monkeypatch):
    (tmp_path / "stock.py").write_text(
        "class Store:\n    def save(self, count):\n        return count\n"
        "def release(store, count, amount):\n    return store.save(count + amount)\n"
        "def unrelated():\n    return 'other'\n"
    )
    (tmp_path / "test_public.py").write_text("def test_public(): assert True\n")
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)

    class LocalSandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    verifier = DeterministicVerifier(sandbox_backend=LocalSandbox(),
        explicit_trusted_executables={"python": sys.executable})
    probe = ReviewerProbe.create(criterion_ids=["stock-release"],
        scenario="Release calls the store; a mismatched quantity fails with its original assertion.",
        code="from stock import release, Store, unrelated\n"
             "def test_release():\n    unrelated()\n    assert release(Store(), 2, 3) == 5\n"
             "def test_mismatch():\n    assert release(Store(), 2, 3) == 9, 'quantity mismatch'\n")
    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        frozen = transaction.freeze(allowed_paths=("stock.py", "test_public.py"), paths_relative_to_cwd=True)
        receipt = run_isolated_review_probe(probe=probe, frozen=frozen,
            check=VerificationCheck("public", "tests", ("python", "-m", "pytest", "-q", "test_public.py"), 20),
            test_path="test_public.py", verifier=verifier, read_roots=(".",), forbidden_paths=(".git", ".env"),
            run_kwargs={"workflow_id": "wf_" + "a" * 32, "task_id": "task_stock", "execution_assignment_id": "asg_stock",
                "patch_digest": frozen.patch_digest, "permissions": PermissionManager(str(tmp_path), prompt=None),
                "permission_cwd": str(tmp_path)})
        receipt = ReviewerProbeLedger().record(probe, frozen.patch_digest, receipt)
        wrapper = {"probeId": probe.probe_id, "candidateDigest": frozen.patch_digest,
                   "receiptRef": "host/receipt.json", "sourceRef": "host/source.json", "receipt": receipt}
    assert receipt["status"] == "failed", receipt
    assert validate_execution_report(receipt["testReport"])
    return wrapper


def read_all(views, request):
    pages, text = [], []
    while True:
        result = views.tool_definition().run(request, None)
        assert result.ok, result.output
        page = json.loads(result.output)
        pages.append(page)
        assert page["displayOnly"] is True
        assert "completeDelivered" not in page
        text.append(page["text"])
        if page["nextCursor"] is None:
            joined = "".join(text)
            assert page["documentDigest"] == content_digest(joined)
            return json.loads(joined), pages
        request = {"cursor": page["nextCursor"]}


def test_real_failed_receipt_compacts_traces_without_losing_failure_or_original(actual_receipt):
    before = deepcopy(actual_receipt)
    views = EvidenceViews(candidate_digest=actual_receipt["candidateDigest"], page_size=800)
    source = {"status": "changes_required", "probeReceipts": [actual_receipt],
              "repairProbes": [{"code": "assert original_requirement()"}],
              "findings": ["Unimplemented quantity rule"]}
    projected = views.view(source, compact=True)
    index = projected["probeReceipts"][0]
    assert index["status"] == "failed" and index["tests"]["failed"] == 1
    assert index["tests"]["passed"] == 1
    assert len(index["failureCases"]) == 1
    assert index["failureCases"][0]["failureKind"] == "assertion"
    assert index["exitCode"] == actual_receipt["receipt"]["evidence"]["exitCode"] == 1
    assert index["evidence"]["stdout"]["preview"] == actual_receipt["receipt"]["evidence"]["stdout"]["preview"]
    assert "previewBase64" not in index["evidence"]["stdout"]
    original_stdout, _ = read_all(views, {"documentId": index["evidence"]["stdout"]["documentRef"]})
    assert original_stdout == actual_receipt["receipt"]["evidence"]["stdout"]
    assert "quantity mismatch" in canonical(index["evidence"])
    assert projected["repairProbes"] == source["repairProbes"]
    assert projected["findings"] == source["findings"]
    execution = index["executionReport"]
    assert execution["validation"] == "valid" and execution["caseCount"] == 2
    assert execution["assertionCount"] == 2 and "cases" not in execution
    assert actual_receipt == before
    original, pages = read_all(views, {"documentId": projected["documentRef"]})
    assert original == source and len(pages) > 1
    # A view is not an alias into either the host receipt or the immutable store.
    index["failureCases"][0]["failureKind"] = "changed"
    index["evidence"].clear()
    assert actual_receipt == before
    unchanged, _ = read_all(views, {"documentId": projected["documentRef"]})
    assert unchanged == original
    assert views.documents.missing([projected["documentRef"]]) == [projected["documentRef"]]


def test_history_index_and_long_stream_excerpts_retain_exact_failed_diagnostics(actual_receipt):
    original = deepcopy(actual_receipt)
    # Surround the actual pytest failure with a long transport preview. This
    # does not invent a receipt or give it verification credit: only its model
    # display is exercised, while actual pytest assertions remain in the log.
    actual_log = original["receipt"]["evidence"]["stdout"]["preview"]
    assert "assert 5 == 9" in actual_log
    wrapped = deepcopy(original)
    wrapped["receipt"]["evidence"]["stdout"]["preview"] = actual_log + ("setup detail\n" * 2000) + actual_log
    source = {"probeId": original["probeId"], "criterionIds": ["stock-release"],
              "scenario": "Validate stock persistence", "purpose": "diagnostic",
              "sourceDigest": original["receipt"]["sourceDigest"], "code": "assert stock_rule()\n" * 1000}
    diagnostic = {"source": source, "purpose": "diagnostic", "receipts": [original, wrapped]}
    decision = {"probeReceipts": [wrapped], "diagnosticObservations": [diagnostic],
                "repairProbes": [{"code": source["code"]}], "requirements": ["Preserve the public stock contract"]}
    before = deepcopy(decision)
    views = EvidenceViews(candidate_digest=original["candidateDigest"], page_size=800)
    projected = views.view(decision, compact=True)
    excerpt = projected["probeReceipts"][0]["evidence"]["stdout"]["preview"]
    assert excerpt["displayOnly"] is True and "Excerpt only" in excerpt["readHint"]
    assert sum(len(part["text"]) for part in excerpt["excerpt"]) <= 1500
    full_log = wrapped["receipt"]["evidence"]["stdout"]["preview"]
    for part in excerpt["excerpt"]:
        assert part["text"] == full_log[part["startOffset"]:part["endOffset"]]
    assert "assert 5 == 9" in canonical(excerpt)
    recovered, pages = read_all(views, {"documentId": excerpt["documentRef"]})
    assert recovered == full_log and len(pages) > 1
    indexed = projected["diagnosticObservations"][0]
    assert "code" not in indexed["source"]
    assert all(indexed["source"][key] == source[key] for key in source if key != "code")
    assert len(indexed["receipts"]) == 2
    for index, receipt in zip(indexed["receipts"], diagnostic["receipts"]):
        assert index["status"] == "failed" and index["exitCode"] == 1
        assert index["failureCases"] == [case for case in receipt["receipt"]["testReport"]["caseReport"]["cases"]
                                         if case["outcome"] != "passed"]
        assert "evidence" not in index
        recovered, _ = read_all(views, {"documentId": index["documentRef"]})
        assert recovered == receipt
    recovered, _ = read_all(views, {"documentId": indexed["source"]["documentRef"]})
    assert recovered == source
    recovered, _ = read_all(views, {"documentId": indexed["documentRef"]})
    assert recovered == diagnostic
    assert projected["repairProbes"] == decision["repairProbes"]
    assert projected["requirements"] == decision["requirements"]
    assert decision == before
    assert views.view(projected, compact=True) == projected
    rebuilt = EvidenceViews(candidate_digest=original["candidateDigest"]).view(before, compact=True)
    assert rebuilt == projected


def test_trace_filters_return_exact_observed_case_assertion_and_consumer_ancestors(actual_receipt):
    views = EvidenceViews(candidate_digest=actual_receipt["candidateDigest"], page_size=800)
    projected = views.view(actual_receipt)
    reference = projected["receipt"]["testReport"]["executionReport"]["documentRef"]
    execution = actual_receipt["receipt"]["testReport"]["executionReport"]
    index, _ = read_all(views, {"documentId": reference, "filters": {}})
    assert len(index["cases"]) == 2 and all("calls" not in case for case in index["cases"])
    case = next(case for case in execution["cases"] if case["nodeId"].endswith("::test_release"))
    filtered, pages = read_all(views, {"documentId": reference, "filters": {
        "caseNodeId": case["nodeId"], "consumer": {"path": "stock.py", "qualname": "Store.save"}}})
    assert filtered["originalDocumentRef"] == reference
    assert filtered["originalDigest"] == content_digest(canonical(execution))
    assert filtered["candidateDigest"] == actual_receipt["candidateDigest"]
    selected = filtered["cases"][0]
    by_id = {call["callId"]: call for call in case["calls"]}
    saved = next(call for call in case["calls"] if call["qualname"] == "Store.save")
    expected = {saved["callId"]}
    while saved["parentCallId"] is not None:
        saved = by_id[saved["parentCallId"]]
        expected.add(saved["callId"])
    assert {call["callId"] for call in selected["calls"]} == expected
    assert all(call in case["calls"] for call in selected["calls"])
    assert not any(call["qualname"] == "unrelated" for call in selected["calls"])
    assert len(pages) > 1
    assertion = case["assertions"][0]
    focused, _ = read_all(views, {"documentId": reference, "filters": {
        "caseNodeId": case["nodeId"], "assertionId": assertion["assertionId"]}})
    assert focused["cases"][0]["assertions"] == [assertion]
    assert focused["cases"][0]["calls"] == []
    missing, _ = read_all(views, {"documentId": reference, "filters": {"consumer": {"qualname": "not_called"}}})
    assert missing["cases"] == []


def test_restored_same_candidate_keeps_document_identity_and_reopens_expired_cursor(actual_receipt):
    original = EvidenceViews(candidate_digest=actual_receipt["candidateDigest"], page_size=800)
    projected = original.view(actual_receipt)
    page = original.read({"documentId": projected["documentRef"]})
    assert page["nextCursor"]
    restored = EvidenceViews(candidate_digest=actual_receipt["candidateDigest"], page_size=800)
    restored_projection = restored.view(json.loads(canonical(actual_receipt)))
    assert restored_projection == projected
    stale = restored.tool_definition().run({"cursor": page["nextCursor"]}, None)
    assert not stale.ok and "expired" in stale.output and "reopen" in stale.output
    reopened, _ = read_all(restored, {"documentId": projected["documentRef"]})
    assert reopened == actual_receipt
    assert original.view(projected) == projected


@pytest.mark.parametrize("invalid_request", [{"cursor": []}, {"documentId": []},
    {"documentId": "missing"}, {"cursor": "missing", "offset": 1}])
def test_invalid_read_requests_have_actionable_tool_errors(invalid_request):
    result = EvidenceViews(candidate_digest="sha256:" + "a" * 64).tool_definition().run(invalid_request, None)
    assert not result.ok
    error = json.loads(result.output)
    assert error["errorCode"] == "completion_evidence_invalid"
    assert error["message"]


def test_candidate_digest_routes_identical_documents_without_accepting_other_store(actual_receipt):
    import jsonschema

    first_digest = actual_receipt["candidateDigest"]
    second_digest = "sha256:" + "b" * 64
    assert second_digest != first_digest
    first = EvidenceViews(candidate_digest=first_digest, page_size=800)
    second = EvidenceViews(candidate_digest=second_digest, page_size=800)
    # Real execution reports can have identical content on two candidate
    # snapshots; content identity alone therefore cannot choose the store.
    report = actual_receipt["receipt"]["testReport"]
    first_ref = first.view(report)["executionReport"]
    second_ref = second.view(report)["executionReport"]
    assert first_ref["documentRef"] == second_ref["documentRef"]
    assert first_ref["candidateDigest"] == first_digest
    assert second_ref["candidateDigest"] == second_digest
    request = {"documentId": first_ref["documentRef"], "candidateDigest": first_digest}
    jsonschema.validate(request, first.tool_definition().input_schema)
    first_page = first.read(request)
    assert first_page["candidateDigest"] == first_digest and first_page["nextCursor"]
    continued = {"cursor": first_page["nextCursor"], "candidateDigest": first_digest}
    jsonschema.validate(continued, first.tool_definition().input_schema)
    assert first.read(continued)["offset"] == first_page["nextOffset"]
    for invalid in (request, {"cursor": "unknown", "candidateDigest": first_digest}):
        result = second.tool_definition().run(invalid, None)
        assert not result.ok and "candidateDigest does not match" in result.output
    result = first.tool_definition().run({"documentId": first_ref["documentRef"], "candidateDigest": []}, None)
    assert not result.ok and "candidateDigest does not match" in result.output
    historical = second.view({"probeReceipts": [actual_receipt]}, compact=True)["probeReceipts"][0]
    assert historical["candidateDigest"] == second_digest
    assert historical["originalCandidateDigest"] == first_digest
    original, _ = read_all(second, {"documentId": historical["documentRef"], "candidateDigest": second_digest})
    assert original == actual_receipt and original["candidateDigest"] == first_digest
    outer = second.view(actual_receipt)
    assert outer["candidateDigest"] == second_digest and outer["originalCandidateDigest"] == first_digest
