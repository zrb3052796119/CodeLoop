"""Regression coverage for durable, same-owner requirement repair feedback."""
from copy import deepcopy

import pytest

from minicode.coverage_audit_state import host_record, record_document
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_execution import implementation_candidate_digest
from minicode.review_evidence_documents import canonical
from minicode.tooling import ToolContext
import test_completion_feedback_chronology as chronology
from test_missing_attribution_order import _exercise

inventory = chronology.inventory


def test_repair_state_keeps_legacy_compatibility_and_rejects_stale_or_modified_authority(inventory):
    case = inventory
    _result, checkpoint, _messages = _exercise(case, interrupt_attribution=False)
    state = checkpoint["reviewState"]
    record = state["acceptedRepairDecision"]
    native = record_document(record)
    context = ToolContext(cwd=str(case["workspace"]))
    digest = checkpoint["candidateDigest"]
    restored = ImplementationCompletionReview.from_state(case["spec"], state)
    assert restored.has_accepted_repair_decision(digest)
    assert restored._accepted_repair_for_candidate(digest, context)["status"] == "changes_required"

    for explicit_none in [False, True]:
        legacy = deepcopy(state)
        legacy.pop("acceptedRepairDecision")
        if explicit_none:
            legacy["acceptedRepairDecision"] = None
        old = ImplementationCompletionReview.from_state(case["spec"], legacy)
        assert not old.has_accepted_repair_decision(digest)
        assert "acceptedRepairDecision" not in old.state
        assert old.state["reviews"] == legacy["reviews"]

    changed = deepcopy(state)
    changed["acceptedRepairDecision"]["rawJson"] += " "
    with pytest.raises(ValueError, match="bytes changed"):
        ImplementationCompletionReview.from_state(case["spec"], changed)

    approved = deepcopy(native)
    approved["decision"]["status"] = "approved"
    changed = deepcopy(state)
    changed["acceptedRepairDecision"] = host_record(record["ref"], canonical(approved))
    with pytest.raises(ValueError, match="repair only"):
        ImplementationCompletionReview.from_state(case["spec"], changed)

    wrong_objective = deepcopy(native)
    wrong_objective["objectiveDigest"] = "sha256:" + "0" * 64
    changed["acceptedRepairDecision"] = host_record(record["ref"], canonical(wrong_objective))
    with pytest.raises(ValueError, match="binding changed"):
        ImplementationCompletionReview.from_state(case["spec"], changed)

    # Even consistently rehashed trusted transport bytes cannot waive the
    # current filesystem/source binding. No arbitrary record imports exist.
    wrong_source = deepcopy(native)
    wrong_source["decision"]["requirements"][1]["sourceEvidence"][0]["sourceDigest"] = "sha256:" + "0" * 64
    authority = record_document(wrong_source["authorityRecord"])
    authority["decision"] = deepcopy(wrong_source["decision"])
    wrong_source["authorityRecord"] = host_record(wrong_source["authorityRecord"]["ref"], canonical(authority))
    changed["acceptedRepairDecision"] = host_record(record["ref"], canonical(wrong_source))
    changed_review = ImplementationCompletionReview.from_state(case["spec"], changed)
    changed_review._cached[digest] = deepcopy(wrong_source["decision"])
    assert changed_review._accepted_repair_for_candidate(digest, context) is None
    assert digest not in changed_review._cached, "Invalid anchors must also evict an old in-memory verdict"

    with (case["workspace"] / "stock.py").open("a") as handle:
        handle.write("\n# A genuinely different temporary candidate.\n")
    current = implementation_candidate_digest(case["spec"], context)
    assert current != digest
    assert not restored.has_accepted_repair_decision(current)
    assert restored._accepted_repair_for_candidate(current, context) is None

    # The append-only native repair record is also enough for host archive
    # reconstruction; rejected drafts and old generic reviews are not promoted.
    archived = ImplementationCompletionReview.from_archive(case["spec"], state["evidenceDirectory"])
    assert archived.has_accepted_repair_decision(digest)
    assert not archived.has_accepted_repair_decision(current)


@pytest.mark.parametrize("failure", ["missing_file", "permission_denied"])
def test_anchor_access_race_drops_old_repair_authority_and_reenters_review(inventory, monkeypatch, failure):
    """An actual anchor read failure after candidate hashing must not strand restore.

    The temporary source is restored immediately after the read to isolate the
    anchor-access race from a normal candidate-change rejection. All checkpoint,
    source validation, review reruns and owner routing remain production code.
    """
    from minicode.agent_loop import run_agent_turn
    from minicode.implementation_continuation import HostImplementationContinuation
    from minicode.permissions import PermissionDeniedError

    case = inventory
    _seed, document, saved = _exercise(case, interrupt_attribution=False)
    restored = HostImplementationContinuation.from_document(document, spec=case["spec"], messages=saved)
    context = ToolContext(cwd=str(case["workspace"]))
    assert implementation_candidate_digest(case["spec"], context) == document["candidateDigest"]
    source = case["workspace"] / "stock.py"
    original_bytes = source.read_bytes()
    original_anchors = ImplementationCompletionReview._anchors
    failures = []
    requests = []
    stop = KeyboardInterrupt("unusable saved anchor falls through to actual native review")

    def actual_access_failure(self, rows, root, permissions):
        if not failures and self.has_accepted_repair_decision(document["candidateDigest"]):
            assert any(row["path"] == "stock.py" for row in rows)
            failures.append(failure)
            if failure == "missing_file":
                source.unlink()
                try:
                    return original_anchors(self, rows, root, permissions)
                finally:
                    source.write_bytes(original_bytes)
            class Denied:
                def ensure_path_access(self, path, action):
                    raise PermissionDeniedError("temporary source read revocation after digest", code="permission_denied")
            return original_anchors(self, rows, root, Denied())
        return original_anchors(self, rows, root, permissions)

    class FirstRequest:
        model_id = "local-anchor-race-control-no-provider"
        def next(self, messages, **kwargs):
            requests.append(set(kwargs["tools_override"].list_all()))
            raise stop

    monkeypatch.setattr(ImplementationCompletionReview, "_anchors", actual_access_failure)
    with pytest.raises(KeyboardInterrupt) as caught:
        run_agent_turn(model=FirstRequest(), messages=saved,
            implementation_continuation=restored, **case["options"])
    assert caught.value is stop
    assert failures == [failure]
    assert len(requests) == 1
    assert "completion_probe_adjudication_submit" in requests[0]
    assert "write_file" not in requests[0], "An unreadable anchor cannot authorize old repair feedback"
    assert source.read_bytes() == original_bytes
    assert implementation_candidate_digest(case["spec"], context) == document["candidateDigest"]
    last_document, _ = case["checkpoints"][-1]
    assert last_document["executorId"] == document["executorId"]
