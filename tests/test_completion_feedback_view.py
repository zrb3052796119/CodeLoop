"""Candidate routing and restoration only; these are not execution receipts."""

from copy import deepcopy
import json

from minicode.completion_feedback_view import CompletionFeedbackViews
from minicode.review_evidence_documents import canonical


A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64


def _message(candidate, attempt, report):
    receipt = {"completionVersion": 1, "executorId": "implementation_" + "1" * 32,
        "attempt": attempt, "continuesSameExecutor": True, "patchDigest": candidate,
        "status": "changes_required", "findings": ["Retained routing fixture"],
        "probeReceipts": [{"probeId": "routing_fixture", "candidateDigest": candidate,
            "receipt": {"status": "failed", "patchDigest": candidate,
                "testReport": {"executionReport": report}}}]}
    return {"role": "user", "_implementation_completion_feedback": True,
        "content": "Host completion routing fixture.\nCompletion evidence:\n" + canonical(receipt)}, receipt


def _display(message):
    return json.loads(message["content"].split("\nCompletion evidence:\n", 1)[1])


def _read(views, request):
    result = views.tool_definition().run(request, None)
    return result, json.loads(result.output)


def _read_all(views, reference, candidate):
    request = {"documentId": reference, "candidateDigest": candidate}
    parts = []
    while True:
        result, page = _read(views, request)
        assert result.ok, page
        assert page["candidateDigest"] == candidate and page["displayOnly"] is True
        assert "completeDelivered" not in page
        assert page["offset"] == sum(map(len, parts))
        parts.append(page["text"])
        if page["nextCursor"] is None:
            return json.loads("".join(parts))
        request = {"cursor": page["nextCursor"], "candidateDigest": candidate}


def _routing_report():
    # This deliberately makes no claim of verified execution. It is a small
    # immutable routing payload whose source bytes are equal across candidates.
    return {"cases": [{"nodeId": "routing_fixture", "complete": False,
        "limitations": ["synthetic routing fixture"], "assertions": [], "calls": []}],
        "junitDigest": "routing-only", "padding": "x" * 9000}


def test_same_execution_document_requires_candidate_and_routes_its_cursors():
    views = CompletionFeedbackViews()
    report = _routing_report()
    displayed = {}
    for number, candidate in enumerate((A, B), 1):
        message, _receipt = _message(candidate, number, report)
        displayed[candidate] = _display(views.project_message(message))
    references = {candidate: value["probeReceipts"][0]["executionReport"]
                  for candidate, value in displayed.items()}
    assert references[A]["documentRef"] == references[B]["documentRef"]
    assert references[A]["candidateDigest"] == A and references[B]["candidateDigest"] == B
    reference = references[B]["documentRef"]
    result, error = _read(views, {"documentId": reference})
    assert not result.ok and error["errorCode"] == "completion_evidence_candidate_required"
    assert set(error["candidateDigests"]) == {A, B}

    result, first = _read(views, {"documentId": reference, "candidateDigest": B})
    assert result.ok and first["candidateDigest"] == B and first["nextCursor"]
    result, error = _read(views, {"cursor": first["nextCursor"], "candidateDigest": A})
    assert not result.ok and error["errorCode"] == "completion_evidence_invalid"
    result, second = _read(views, {"cursor": first["nextCursor"], "candidateDigest": B})
    assert result.ok and second["candidateDigest"] == B
    assert second["offset"] == len(first["text"]) and second["nextCursor"] is None
    assert json.loads(first["text"] + second["text"]) == report
    # A cursor is already scoped to its issuing store; omitting a candidate
    # must never make another candidate's store answer it.
    result, repeated = _read(views, {"cursor": first["nextCursor"]})
    assert result.ok and repeated == second
    result, filtered = _read(views, {"documentId": reference, "candidateDigest": B, "filters": {}})
    assert result.ok and filtered["candidateDigest"] == B
    assert json.loads(filtered["text"])["candidateDigest"] == B


def test_replaced_feedback_restores_old_parent_receipt_and_execution_refs():
    views = CompletionFeedbackViews()
    report = _routing_report()
    old_message, old_receipt = _message(A, 1, report)
    new_message, new_receipt = _message(B, 2, report)
    before = deepcopy((old_message, new_message, old_receipt, new_receipt))
    old_feedback = views.project_message(old_message)
    old_display = _display(old_feedback)
    latest_feedback = views.project_message(new_message)
    latest_before_restore = deepcopy(latest_feedback)
    assert latest_feedback["_completion_evidence_history"] == [old_message["content"]]
    assert latest_feedback["_completion_evidence_original"] == new_message["content"]
    assert "_completion_evidence_history" not in _display(latest_feedback)

    # The replaced feedback itself is gone from model history. Only the latest
    # host message survives a native checkpoint with its flat original history.
    restored = CompletionFeedbackViews()
    rebuilt_latest = restored.project_message(deepcopy(latest_feedback))
    assert rebuilt_latest == latest_feedback
    assert _read_all(restored, old_display["documentRef"], A) == old_receipt
    assert _read_all(restored, _display(rebuilt_latest)["documentRef"], B) == new_receipt
    old_execution = old_display["probeReceipts"][0]["executionReport"]
    assert _read_all(restored, old_execution["documentRef"], A) == report
    result, error = _read(restored, {"documentId": old_execution["documentRef"]})
    assert not result.ok and error["errorCode"] == "completion_evidence_candidate_required"
    assert set(error["candidateDigests"]) == {A, B}
    assert (old_message, new_message, old_receipt, new_receipt) == before
    assert latest_feedback == latest_before_restore


def test_host_approval_status_keeps_exact_evidence_without_replaying_all_witnesses():
    _feedback, receipt = _message(A, 1, _routing_report())
    receipt.update(status="approved", continuesSameExecutor=False,
                   coverageAudit={"witnesses": ["retained original mapping" * 1000]})
    original = {"role": "system", "_implementation_completion_status": True,
        "content": "Current host completion decision (supersedes prior repair feedback):\n" + canonical(receipt)}
    views = CompletionFeedbackViews()
    projected = views.project_message(original)
    display = json.loads(projected["content"].split("\n", 1)[1])
    assert len(projected["content"]) < 1500
    assert display["status"] == "approved" and display["attempt"] == 1
    assert projected["_completion_evidence_original"] == original["content"]
    assert _read_all(views, display["documentRef"], A) == receipt
