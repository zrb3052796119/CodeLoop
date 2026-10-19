"""An early concrete rejection is deliverable without inventing other verdicts."""

import copy
import json
from dataclasses import replace

import pytest

from minicode.multi_agent_contracts import parse_assignment, parse_role_result
from minicode.scheduler_artifacts import SchedulerArtifactStore
from minicode.tooling import ToolContext, ToolRegistry
from test_multi_agent_v2_execution_contracts import _executor_assignment


PATCH = "sha256:" + "b" * 64


def _bound(*, build=True):
    from minicode.reviewer_result_submission import create_reviewer_submit_tool

    refs = ("artifact_matrix", "artifact_diff", "artifact_verification", "artifact_check_a", "artifact_check_b")
    assignment = parse_assignment(_executor_assignment(
        role="reviewer", mode="change_review", expectedResultType="ReviewVerdict.v1",
        inputRefs=list(refs),
    ))
    matrix = {
        "matrixVersion": 1, "resultType": "AcceptanceEvidenceMatrix.v1",
        "workflowId": assignment.workflow_id, "taskId": assignment.task_id,
        "patchDigest": PATCH, "frozenDiffRef": "artifact_diff",
        "verificationArtifactRef": "artifact_verification", "criteria": [
            {"criterionId": "ac_behavior", "checkIds": ["check_a"], "evidenceRefs": ["artifact_check_a"]},
            {"criterionId": "ac_tests", "checkIds": ["check_b"], "evidenceRefs": ["artifact_check_b"]},
        ],
    }
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    for ref in refs:
        document = matrix if ref == "artifact_matrix" else {"patchDigest": PATCH}
        store.publish(ref, document, authority=authority)
    channel = store.create_assignment_channel(readable_keys=refs, write_key="artifact_review")
    kwargs = dict(patch_digest=PATCH, matrix=matrix, matrix_ref="artifact_matrix", diff_ref="artifact_diff")
    tool = create_reviewer_submit_tool(assignment, channel, **kwargs) if build else None
    return assignment, store, channel, tool, kwargs


def _approval():
    return {
        "verdict": "approved", "criteria": [
            {"criterionId": "ac_behavior", "status": "satisfied"},
            {"criterionId": "ac_tests", "status": "satisfied"},
        ], "findings": [], "warnings": [], "requestedChecks": [], "confidence": "high",
    }


def _rejection():
    return {
        "verdict": "changes_required",
        "criteria": [{"criterionId": "ac_tests", "status": "failed"}],
        "findings": [{"criterionIds": ["ac_tests"], "statement": "The public tests never assert the failure path."}],
        "warnings": [], "requestedChecks": [], "confidence": "high",
    }


def _execute(tool, channel, value):
    return ToolRegistry([tool]).execute(tool.name, value, ToolContext(_subagent_mailbox=channel))


def test_early_rejection_binds_evidence_and_leaves_unreviewed_criteria_unknown():
    assignment, store, channel, tool, _ = _bound()
    result = _execute(tool, channel, _rejection())
    assert result.ok, result.output
    assert result._implementation_submitted
    document = json.loads(store.read(channel.write_key).content)
    parsed = parse_role_result(assignment, document)
    assert parsed["resultType"] == "ReviewVerdict.v1"
    assert document["assignmentId"] == assignment.assignment_id
    assert document["patchDigest"] == PATCH
    assert document["criteria"] == [
        {"criterionId": "ac_behavior", "status": "unknown", "evidenceRefs": []},
        {"criterionId": "ac_tests", "status": "failed", "evidenceRefs": ["artifact_matrix", "artifact_diff", "artifact_check_b"]},
    ]
    assert document["blockingFindings"][0]["evidenceRefs"] == ["artifact_matrix", "artifact_diff", "artifact_check_b"]
    assert "ac_tests" in document["blockingFindings"][0]["statement"]
    assert "public tests never assert" in document["blockingFindings"][0]["statement"]
    assert not _execute(tool, channel, _approval()).ok


def test_approval_requires_explicit_judgments_and_uses_original_mapping():
    assignment, store, channel, tool, kwargs = _bound()
    # Caller mutation after binding cannot change the frozen evidence mapping.
    kwargs["matrix"]["criteria"][0]["evidenceRefs"][:] = ["artifact_check_b"]
    result = _execute(tool, channel, _approval())
    assert result.ok, result.output
    document = json.loads(store.read(channel.write_key).content)
    assert parse_role_result(assignment, document)["verdict"] == "approved"
    assert document["criteria"][0]["evidenceRefs"] == ["artifact_matrix", "artifact_diff", "artifact_check_a"]
    assert document["criteria"][1]["evidenceRefs"] == ["artifact_matrix", "artifact_diff", "artifact_check_b"]


def test_finding_may_link_related_known_criteria_without_claiming_they_were_reviewed():
    assignment, store, channel, tool, _ = _bound()
    value = _rejection()
    value["findings"][0]["criterionIds"] = ["ac_tests", "ac_behavior"]
    result = _execute(tool, channel, value)
    assert result.ok, result.output
    document = parse_role_result(assignment, json.loads(store.read(channel.write_key).content))
    assert document["criteria"][0]["status"] == "unknown"
    assert document["criteria"][1]["status"] == "failed"
    assert document["blockingFindings"][0]["evidenceRefs"] == [
        "artifact_matrix", "artifact_diff", "artifact_check_b", "artifact_check_a",
    ]


@pytest.mark.parametrize("other_status", ["satisfied", "unknown", None])
def test_mixed_negative_review_preserves_every_finding_in_immutable_result(other_status):
    from minicode.workflow_repair import review_supports_implementation_repair

    assignment, store, channel, tool, kwargs = _bound()
    value = _rejection()
    if other_status is not None:
        value["criteria"].append({"criterionId": "ac_behavior", "status": other_status})
    value["findings"].append({
        "criterionIds": ["ac_behavior"],
        "statement": "The implementation preserves the compatible public behavior.",
    })
    original = copy.deepcopy(value)
    # Post-binding caller mutation cannot reassign the extra finding's evidence.
    kwargs["matrix"]["criteria"][0]["evidenceRefs"][:] = ["artifact_check_b"]
    result = _execute(tool, channel, value)
    assert result.ok and result._implementation_submitted, result.output
    assert value == original
    channel.close()
    artifact = store.read(channel.write_key)
    assert artifact is not None, "the accepted result must survive capability revocation"
    document = parse_role_result(assignment, json.loads(artifact.content))
    assert document["verdict"] == "changes_required"
    assert document["patchDigest"] == PATCH
    assert [item["status"] for item in document["criteria"]] == [other_status or "unknown", "failed"]
    assert document["blockingFindings"] == [
        {"id": "finding_1", "severity": "high",
         "statement": "[ac_tests] " + original["findings"][0]["statement"],
         "evidenceRefs": ["artifact_matrix", "artifact_diff", "artifact_check_b"]},
        {"id": "finding_2", "severity": "high",
         "statement": "[ac_behavior] " + original["findings"][1]["statement"],
         "evidenceRefs": ["artifact_matrix", "artifact_diff", "artifact_check_a"]},
    ]
    assert review_supports_implementation_repair(document)
    assert not document["warnings"], "no finding may be silently downgraded or discarded"


@pytest.mark.parametrize("invalid", ["uncovered_failure", "no_failed_criterion", "approval", "inconclusive", "unknown_finding"])
def test_mixed_findings_cannot_weaken_rejection_or_approval_contract(invalid):
    _, store, channel, tool, _ = _bound()
    value = _rejection()
    value["criteria"].append({"criterionId": "ac_behavior", "status": "satisfied"})
    if invalid == "uncovered_failure":
        value["criteria"][-1]["status"] = "failed"
    else:
        value["findings"].append({"criterionIds": ["ac_behavior"], "statement": "The behavior remains compatible."})
        if invalid == "no_failed_criterion": value["criteria"][0]["status"] = "satisfied"
        elif invalid == "approval":
            value["verdict"] = "approved"
            value["criteria"][0]["status"] = "satisfied"
        elif invalid == "inconclusive": value["verdict"] = "inconclusive"
        elif invalid == "unknown_finding": value["findings"][-1]["criterionIds"] = ["other"]
    result = _execute(tool, channel, value)
    assert not result.ok and not result._implementation_submitted
    assert store.read(channel.write_key) is None
    assert _execute(tool, channel, _rejection()).ok, "rejection cannot consume the result key"


def test_inconclusive_preserves_unknowns_checks_and_warnings_without_auto_approval():
    assignment, store, channel, tool, _ = _bound()
    value = dict(_approval(), verdict="inconclusive", criteria=[],
                 warnings=["The changed branch cannot yet be established from the supplied evidence."],
                 requestedChecks=["pytest tests/test_auth.py -q"], confidence="low")
    result = _execute(tool, channel, value)
    assert result.ok, result.output
    document = parse_role_result(assignment, json.loads(store.read(channel.write_key).content))
    assert document["verdict"] == "inconclusive"
    assert all(item["status"] == "unknown" for item in document["criteria"])
    assert document["warnings"][0]["severity"] == "medium"
    assert document["requestedChecks"] == value["requestedChecks"]


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(assignmentId="asg_forged"),
    lambda v: v.update(patchDigest=PATCH),
    lambda v: v.pop("warnings"),
    lambda v: v.update(verdict="passed"),
    lambda v: v.update(verdict=[]),
    lambda v: v.update(confidence=[]),
    lambda v: v.update(criteria=[]),
    lambda v: v["criteria"].pop(),
    lambda v: v["criteria"][0].update(status="unknown"),
    lambda v: v["criteria"][0].update(status=[]),
    lambda v: v["criteria"][0].update(criterionId="other"),
    lambda v: v["criteria"].append(v["criteria"][0]),
    lambda v: v["criteria"][0].update(evidenceRefs=["forged"]),
    lambda v: v.update(requestedChecks=["pytest tests/test_auth.py"]),
    lambda v: v.update(warnings=[" "]),
    lambda v: v.update(warnings=["x" * 4001]),
    lambda v: v.update(warnings=["duplicate", "duplicate"]),
    lambda v: v.update(warnings=["warning"] * 41),
    lambda v: v.update(requestedChecks=["check"] * 41),
    lambda v: v.update(requestedChecks=["x" * 501]),
])
def test_invalid_approval_does_not_consume_result_capability(mutate):
    _, store, channel, tool, _ = _bound()
    invalid = _approval()
    mutate(invalid)
    result = _execute(tool, channel, invalid)
    assert not result.ok and not result._implementation_submitted
    assert store.read(channel.write_key) is None
    assert _execute(tool, channel, _approval()).ok


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(findings=[]),
    lambda v: v.update(criteria=[]),
    lambda v: v["criteria"][0].update(status="unknown"),
    lambda v: v["findings"][0].update(criterionIds=[]),
    lambda v: v["findings"][0].update(criterionIds=["other"]),
    lambda v: v["findings"][0].update(criterionIds=["ac_tests", "ac_tests"]),
    lambda v: v["findings"][0].update(criterionIds=["ac_behavior"]),
    lambda v: v["findings"][0].update(statement=" "),
    lambda v: v["findings"][0].update(statement="bad\x00text"),
    lambda v: v["findings"][0].update(statement="x" * 4001),
    lambda v: v["findings"][0].update(evidenceRefs=["forged"]),
    lambda v: v["findings"].append(copy.deepcopy(v["findings"][0])),
])
def test_invalid_or_unbound_findings_do_not_consume_result_capability(mutate):
    _, store, channel, tool, _ = _bound()
    invalid = _rejection()
    mutate(invalid)
    assert not _execute(tool, channel, invalid).ok
    assert store.read(channel.write_key) is None
    assert _execute(tool, channel, _rejection()).ok


def test_owner_revocation_and_direct_run_validation_remain_enforced():
    _, store, channel, tool, _ = _bound()
    other = store.create_assignment_channel(readable_keys=channel.readable_keys, write_key="artifact_other_review")
    result = _execute(tool, other, _approval())
    assert not result.ok and "owner" in result.output
    assert store.read(channel.write_key) is None
    assert not tool.run(dict(_approval(), assignmentId="asg_forged"), ToolContext(_subagent_mailbox=channel)).ok
    channel.close()
    assert not _execute(tool, channel, _approval()).ok
    assert store.read(channel.write_key) is None


@pytest.mark.parametrize("value", [[], None, '{"verdict":"approved","verdict":"changes_required"}',
    '{"criteria":[{"criterionId":"ac_tests","status":"failed","status":"satisfied"}]}'])
def test_nonobjects_and_duplicate_field_json_are_not_coerced(value):
    # This boundary receives parsed objects. Raw transport duplicate keys may
    # already have been collapsed by the adapter; this is not an adapter test.
    _, store, channel, tool, _ = _bound()
    assert not _execute(tool, channel, value).ok
    assert store.read(channel.write_key) is None
    assert _execute(tool, channel, _rejection()).ok


@pytest.mark.parametrize("change", [
    {"patch_digest": "sha256:" + "a" * 64},
    {"matrix_ref": "artifact_check_a"},
    {"diff_ref": "artifact_check_a"},
])
def test_factory_rejects_rebound_host_artifacts(change):
    from minicode.reviewer_result_submission import create_reviewer_submit_tool
    assignment, _, channel, _, kwargs = _bound(build=False)
    with pytest.raises(ValueError):
        create_reviewer_submit_tool(assignment, channel, **(kwargs | change))


def test_factory_rejects_wrong_role_and_mutated_matrix():
    from minicode.reviewer_result_submission import create_reviewer_submit_tool
    from minicode.multi_agent_contracts import WorkerRole
    assignment, _, channel, _, kwargs = _bound(build=False)
    with pytest.raises(ValueError):
        create_reviewer_submit_tool(replace(assignment, role=WorkerRole.EXECUTOR), channel, **kwargs)
    kwargs["matrix"]["criteria"][0]["evidenceRefs"] = ["artifact_check_b"]
    with pytest.raises(ValueError):
        create_reviewer_submit_tool(assignment, channel, **kwargs)


def test_total_bound_applies_after_host_evidence_expansion():
    _, store, channel, tool, _ = _bound()
    value = dict(_rejection(), warnings=[letter * 4000 for letter in "ABCD"] + ["E" * 1200])
    assert len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) < 18000
    result = _execute(tool, channel, value)
    assert not result.ok and "byte" in result.output
    assert store.read(channel.write_key) is None
    assert _execute(tool, channel, _rejection()).ok


def test_concurrent_valid_submissions_publish_exactly_one_decision():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    _, store, channel, tool, _ = _bound()
    start = Barrier(2)

    def submit(value):
        start.wait()
        return _execute(tool, channel, value)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, value) for value in (_approval(), _rejection())]
        results = [future.result(timeout=5) for future in futures]
    assert sum(result.ok for result in results) == 1
    assert sum(result._implementation_submitted for result in results) == 1
    assert json.loads(store.read(channel.write_key).content)["verdict"] in ("approved", "changes_required")


def _native(value):
    value = copy.deepcopy(value)
    value["blockingFindings"] = value.pop("findings")
    return value


def test_reviewer_can_explicitly_withdraw_requested_checks_then_omit_empty_field():
    assignment, store, channel, tool, _ = _bound()
    first = _native(_approval())
    first["requestedChecks"] = ["Add a negative regression case."]
    original = copy.deepcopy(first)
    rejected = _execute(tool, channel, first)
    assert not rejected.ok and rejected._review_submission_rejected
    assert "no findings or requested checks" in rejected.output
    assert store.read(channel.write_key) is None and first == original

    corrected = copy.deepcopy(first)
    del corrected["requestedChecks"]
    corrected["observations"] = [{"criterionIds": ["ac_tests"],
                                  "statement": "A further negative regression would be optional."}]
    result = _execute(tool, channel, corrected)
    assert result.ok and result._implementation_submitted, result.output
    assert "requestedChecks" not in corrected, "normalization must not mutate model arguments"
    assert "requestedChecks" not in tool.input_schema["required"]
    document = json.loads(store.read(channel.write_key).content)
    assert document["requestedChecks"] == []
    assert parse_role_result(assignment, document)["verdict"] == "approved"
    assert not document["blockingFindings"]
    assert document["warnings"][0]["statement"].endswith(corrected["observations"][0]["statement"])
    del document["requestedChecks"]
    with pytest.raises(ValueError):
        parse_role_result(assignment, document)


@pytest.mark.parametrize("checks", [None, "", {}, False, ["A required check remains."]])
def test_requested_checks_default_never_replaces_an_explicit_invalid_value(checks):
    _, store, channel, tool, _ = _bound()
    value = _native(_approval())
    value["requestedChecks"] = checks
    result = _execute(tool, channel, value)
    assert not result.ok and not result._implementation_submitted
    assert store.read(channel.write_key) is None


def test_requested_checks_omission_does_not_allow_unknown_fields():
    _, store, channel, tool, _ = _bound()
    value = _native(_approval())
    del value["requestedChecks"]
    value["requestedCheck"] = []
    result = _execute(tool, channel, value)
    assert not result.ok and "unknown fields: requestedCheck" in result.output
    assert store.read(channel.write_key) is None


def test_public_native_schema_and_observations_preserve_explicit_approval_evidence():
    assignment, store, channel, tool, _ = _bound()
    value = _native(_approval())
    value["observations"] = [{
        "criterionIds": ["ac_tests"],
        "statement": "The regression test asserts the previously missing branch.",
    }]
    value["warnings"] = ["The optional diagnostic wording was not assessed."]
    original = copy.deepcopy(value)
    result = _execute(tool, channel, value)
    assert result.ok and result._implementation_submitted, result.output
    assert "findings" not in tool.input_schema["properties"]
    assert "blockingFindings" in tool.input_schema["required"]
    assert "observations" in tool.input_schema["properties"]
    assert value == original
    document = parse_role_result(assignment, json.loads(store.read(channel.write_key).content))
    assert document["verdict"] == "approved" and not document["blockingFindings"]
    assert document["warnings"] == [
        {"id": "warning_1", "severity": "medium", "statement": value["warnings"][0],
         "evidenceRefs": ["artifact_matrix", "artifact_diff"]},
        {"id": "observation_1", "severity": "low",
         "statement": "[observation: ac_tests] " + value["observations"][0]["statement"],
         "evidenceRefs": ["artifact_matrix", "artifact_diff", "artifact_check_b"]},
    ]
    assert all(row["status"] == "satisfied" for row in document["criteria"])


@pytest.mark.parametrize("legacy", [False, True])
def test_positive_blocking_text_cannot_be_silently_converted_to_approval(legacy):
    _, store, channel, tool, _ = _bound()
    value = _approval() if legacy else _native(_approval())
    key = "findings" if legacy else "blockingFindings"
    value[key] = [{"criterionIds": ["ac_tests"], "statement": "All tests pass."}]
    result = _execute(tool, channel, value)
    assert not result.ok and not result._implementation_submitted
    assert "approved requires empty blockingFindings" in result.output
    assert "explicitly failed criterion" not in result.output
    assert store.read(channel.write_key) is None
    # Only an explicit subsequent model decision can remove the blocker.
    assert _execute(tool, channel, _native(_approval())).ok


@pytest.mark.parametrize("legacy", [False, True])
def test_native_and_legacy_negative_fields_keep_blockers_and_unknowns(legacy):
    assignment, store, channel, tool, _ = _bound()
    value = _rejection() if legacy else _native(_rejection())
    value["observations"] = [{"criterionIds": ["ac_behavior"], "statement": "The implementation branch was inspected."}]
    result = _execute(tool, channel, value)
    assert result.ok, result.output
    document = parse_role_result(assignment, json.loads(store.read(channel.write_key).content))
    assert document["verdict"] == "changes_required"
    assert document["criteria"][0]["status"] == "unknown"
    assert document["criteria"][1]["status"] == "failed"
    assert document["blockingFindings"][0]["severity"] == "high"
    assert document["blockingFindings"][0]["statement"] == "[ac_tests] " + _rejection()["findings"][0]["statement"]


@pytest.mark.parametrize("problem", ["aliases", "unknown", "empty", "duplicate", "overflow", "uncovered_failure"])
def test_observations_never_expand_authority_or_replace_a_required_blocker(problem):
    _, store, channel, tool, _ = _bound()
    value = _native(_approval())
    value["observations"] = [{"criterionIds": ["ac_tests"], "statement": "Observed the test assertion."}]
    if problem == "aliases":
        value["findings"] = []
    elif problem == "unknown":
        value["observations"][0]["criterionIds"] = ["foreign"]
    elif problem == "empty":
        value["observations"][0]["criterionIds"] = []
    elif problem == "duplicate":
        value["observations"] *= 2
    elif problem == "overflow":
        value["warnings"] = [f"Warning {number}" for number in range(40)]
    else:
        value["verdict"] = "changes_required"
        value["criteria"][1]["status"] = "failed"
    result = _execute(tool, channel, value)
    assert not result.ok and not result._implementation_submitted
    assert store.read(channel.write_key) is None
    assert _execute(tool, channel, _native(_rejection())).ok


def test_only_bound_live_semantic_rejection_has_host_retry_marker():
    from minicode.reviewer_result_submission import reviewer_submission_rejected
    from minicode.tooling import ToolResult

    _, store, channel, tool, _ = _bound()
    invalid = _approval()
    invalid["findings"] = [{"criterionIds": ["ac_tests"], "statement": "All tests pass."}]
    result = _execute(tool, channel, invalid)
    assert not result.ok and reviewer_submission_rejected(tool, result)
    assert "_review_submission_rejected" not in repr(result)
    assert not reviewer_submission_rejected(tool, ToolResult(ok=False, output=result.output))
    forged = replace(tool, run=lambda *_: result)
    assert not reviewer_submission_rejected(forged, result)
    assert store.read(channel.write_key) is None
    accepted = _execute(tool, channel, _native(_approval()))
    assert accepted.ok and not reviewer_submission_rejected(tool, accepted)
    assert not reviewer_submission_rejected(tool, result), "a consumed channel cannot grant another attempt"
    assert not _execute(tool, channel, invalid)._review_submission_rejected


@pytest.mark.parametrize("failure", ["foreign_owner", "closed", "revoked"])
def test_owner_and_revocation_failures_cannot_grant_review_retry(failure):
    from minicode.reviewer_result_submission import reviewer_submission_rejected

    _, store, channel, tool, _ = _bound()
    context_channel = channel
    if failure == "foreign_owner":
        context_channel = store.create_assignment_channel(readable_keys=channel.readable_keys,
                                                         write_key="artifact_foreign_review")
    elif failure == "closed":
        channel.close()
    else:
        store.revoke_pending_assignment_writes(authority=store._scheduler_authority)
    result = _execute(tool, context_channel, {})
    assert not result.ok and not result._review_submission_rejected
    assert not reviewer_submission_rejected(tool, result)
    assert store.read(channel.write_key) is None
