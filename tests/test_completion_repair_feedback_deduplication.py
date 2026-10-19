"""Only the exact delivered host decision can suppress a repeat handoff."""
from copy import deepcopy
import json

from minicode.completion_repair_decision import delivered_repair_matches
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.tooling import ToolContext
import test_missing_attribution_order as ordering

_exercise = ordering._exercise
inventory = ordering.inventory


def test_native_feedback_comparison_keeps_full_semantics_owner_and_candidate(inventory):
    case = inventory
    _result, document, _messages = _exercise(case, interrupt_attribution=False)
    restored = ImplementationCompletionReview.from_state(case["spec"], document["reviewState"])
    decision = restored._accepted_repair_for_candidate(document["candidateDigest"],
        ToolContext(cwd=str(case["workspace"])))
    feedback = document["completionFeedback"]
    bindings = {"executor_id": document["executorId"], "candidate_digest": document["candidateDigest"],
                "objective": case["spec"].objective}
    assert delivered_repair_matches(feedback, decision, **bindings)

    # Current feedback is a display projection. Equality must use its retained
    # exact original, not the compact model-facing receipt or history entries.
    assert "_completion_evidence_original" in feedback
    display = {**feedback, "content": "A display-only summary is not repair authority."}
    assert delivered_repair_matches(display, decision, **bindings)
    moved = {**decision, "evidenceRef": "/new/review.json", "repairDecisionRef": "/new/repair.json",
             "evidenceDirectory": "/new"}
    assert delivered_repair_matches(feedback, moved, **bindings)

    changed = deepcopy(decision)
    changed["findings"].append("An additional independently established requirement needs repair.")
    assert not delivered_repair_matches(feedback, changed, **bindings)
    changed = deepcopy(decision)
    changed["requirements"][1]["rationale"] += " Additional semantic evidence."
    assert not delivered_repair_matches(feedback, changed, **bindings)
    changed = deepcopy(decision)
    changed["probeReceipts"][0]["receiptRef"] = "/other/native-receipt.json"
    assert not delivered_repair_matches(feedback, changed, **bindings)
    changed = {**decision, "newHostObligation": "A new field must not be silently ignored."}
    assert not delivered_repair_matches(feedback, changed, **bindings)
    changed = {**decision, "completionReviewVersion": True}
    assert not delivered_repair_matches(feedback, changed, **bindings)
    assert not delivered_repair_matches(feedback, {**decision, "canContinue": False}, **bindings)

    original = feedback["_completion_evidence_original"]
    prefix, marker, body = original.rpartition("\nCompletion evidence:\n")
    receipt = json.loads(body)
    for field, value in (("executorId", "implementation_" + "0" * 32),
                         ("patchDigest", "sha256:" + "0" * 64),
                         ("continuesSameExecutor", False)):
        prior = {**receipt, field: value}
        other_feedback = {"role": "user", "_implementation_completion_feedback": True,
                          "content": prefix + marker + json.dumps(prior)}
        assert not delivered_repair_matches(other_feedback, decision, **bindings), field
    projection = deepcopy(feedback)
    projection["_completion_evidence_projection"]["originalContentDigest"] = "sha256:" + "0" * 64
    assert not delivered_repair_matches(projection, decision, **bindings)
