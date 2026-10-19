"""Requested checks are feedback, never authority to run new commands."""

from copy import deepcopy

import pytest

from minicode.workflow_repair import review_supports_implementation_repair


def _review():
    return {
        "verdict": "changes_required",
        "criteria": [
            {"criterionId": "c1", "status": "failed"},
            {"criterionId": "c2", "status": "unknown"},
        ],
        "blockingFindings": [{
            "id": "finding_1", "severity": "high",
            "statement": "[c1] The existing test does not assert the required behavior.",
            "evidenceRefs": ["artifact_diff"],
        }],
        "requestedChecks": ["Add a behavioral assertion under the existing approved check."],
    }


@pytest.mark.parametrize("remaining_status", ["unknown", "satisfied", "failed"])
@pytest.mark.parametrize("verdict", ["changes_required", "inconclusive"])
def test_requested_checks_do_not_discard_a_known_repairable_defect(remaining_status, verdict):
    review = _review()
    review["verdict"] = verdict
    review["criteria"][1]["status"] = remaining_status
    original = deepcopy(review)

    assert review_supports_implementation_repair(review)
    # The eligibility predicate must not accept a review, remove its requested
    # checks, or rewrite text into executable verification authority.
    assert review == original


@pytest.mark.parametrize("missing_authority", [
    "no_failed_criterion", "no_finding", "blank_finding", "critical_finding",
    "approved", "inconclusive_without_defect", "invalid_criterion_status",
])
def test_requested_checks_alone_cannot_authorize_implementation_repair(missing_authority):
    review = _review()
    if missing_authority == "no_failed_criterion":
        review["criteria"][0]["status"] = "satisfied"
    elif missing_authority == "no_finding":
        review["blockingFindings"] = []
    elif missing_authority == "blank_finding":
        review["blockingFindings"][0]["statement"] = " "
    elif missing_authority == "critical_finding":
        review["blockingFindings"][0]["severity"] = "critical"
    elif missing_authority == "invalid_criterion_status":
        review["criteria"][1]["status"] = "unchecked"
    elif missing_authority == "inconclusive_without_defect":
        review["verdict"] = "inconclusive"
        review["criteria"][0]["status"] = "unknown"
    else:
        review["verdict"] = missing_authority

    assert not review_supports_implementation_repair(review)
