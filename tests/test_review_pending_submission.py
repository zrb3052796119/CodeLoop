"""A valid unfinished review keeps its exact result capability available."""

import copy
import inspect
import json
from dataclasses import replace

import pytest

from minicode import reviewer_result_submission as submission
from minicode.multi_agent_contracts import parse_role_result
from minicode.tooling import ToolResult
from minicode.tools.subagent_notes import subagent_note_write_tool
from test_reviewer_result_submission import _approval, _bound, _execute, _rejection


def _unknown():
    return dict(_approval(), verdict="inconclusive", criteria=[
        {"criterionId": "ac_behavior", "status": "satisfied"},
        {"criterionId": "ac_tests", "status": "unknown"},
    ], confidence="low")


def _pending_guard(declaration, context):
    assert declaration["resultType"] == "ReviewVerdict.v1"
    assert context._subagent_mailbox is not None
    if (declaration["verdict"] == "inconclusive"
            and not declaration["blockingFindings"]
            and not declaration["requestedChecks"]
            and any(row["status"] == "unknown" for row in declaration["criteria"])
            and not any(row["status"] == "failed" for row in declaration["criteria"])):
        return {"status": "pending_review", "reviewReceived": False,
                "unknownCriterionIds": [row["criterionId"] for row in declaration["criteria"]
                                        if row["status"] == "unknown"]}
    return None


def _tools(guard=_pending_guard):
    assignment, store, channel, _, kwargs = _bound(build=False)
    # The frozen implementation must reach its old behavior, not fail importing
    # a new interface. On that version, a legal inconclusive result is published.
    if "continuation_guard" in inspect.signature(submission.create_reviewer_submit_tool).parameters:
        kwargs["continuation_guard"] = guard
    native = submission.create_reviewer_submit_tool(assignment, channel, **kwargs)
    binder = getattr(submission, "bind_reviewer_legacy_submission_tool", lambda _, original: original)
    return assignment, store, channel, native, binder(native, subagent_note_write_tool)


def _legacy_value(value, key):
    assignment, store, channel, native, _ = _bound()
    assert _execute(native, channel, value).ok
    declaration = json.loads(store.read(channel.write_key).content)
    parse_role_result(assignment, declaration)
    return {"key": key, "content": json.dumps(declaration)}


@pytest.mark.parametrize("legacy", [False, True])
def test_valid_unknown_review_stays_pending_then_same_channel_accepts_completion(legacy):
    assignment, store, channel, native, note = _tools()
    tool = note if legacy else native
    value = _legacy_value(_unknown(), channel.write_key) if legacy else _unknown()
    original = copy.deepcopy(value)
    for _ in range(3):
        result = _execute(tool, channel, value)
        assert result.ok, result.output
        assert not result._implementation_submitted
        assert store.read(channel.write_key) is None, "unfinished review must not consume the result key"
        assert json.loads(result.output)["status"] == "pending_review"
        assert submission.reviewer_submission_needs_evidence(tool, result)
        assert not submission.reviewer_submission_rejected(tool, result)
    assert value == original
    assert submission.reviewer_submission_same_channel(tool, native)
    accepted = _execute(native, channel, _approval())
    assert accepted.ok and accepted._implementation_submitted
    assert parse_role_result(assignment, json.loads(store.read(channel.write_key).content))["verdict"] == "approved"
    assert not submission.reviewer_submission_needs_evidence(tool, result)
    assert not submission.reviewer_submission_same_channel(tool, native)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("verdict", ["changes_required", "inconclusive"])
def test_concrete_negative_keeps_original_terminal_feedback(legacy, verdict):
    assignment, store, channel, native, note = _tools()
    value = dict(_rejection(), verdict=verdict)
    tool = note if legacy else native
    result = _execute(tool, channel, _legacy_value(value, channel.write_key) if legacy else value)
    assert result.ok, result.output
    assert not submission.reviewer_submission_needs_evidence(tool, result)
    document = parse_role_result(assignment, json.loads(store.read(channel.write_key).content))
    from minicode.workflow_repair import review_supports_implementation_repair
    assert review_supports_implementation_repair(document)
    assert document["verdict"] == verdict
    assert document["criteria"][1]["status"] == "failed"


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("failure", ["owner", "closed", "revoked", "revoked_in_guard"])
def test_pending_never_renews_owner_or_closed_authority(legacy, failure):
    calls = []

    def guard(declaration, context):
        calls.append(declaration)
        if failure == "revoked_in_guard":
            context._subagent_mailbox.close()
        return _pending_guard(declaration, context)

    _, store, channel, native, note = _tools(guard)
    tool = note if legacy else native
    context_channel = channel
    if failure == "owner":
        context_channel = store.create_assignment_channel(readable_keys=channel.readable_keys,
                                                         write_key="artifact_other_review")
    elif failure == "closed":
        channel.close()
    elif failure == "revoked":
        store.revoke_pending_assignment_writes(authority=store._scheduler_authority)
    value = _legacy_value(_unknown(), channel.write_key) if legacy else _unknown()
    result = _execute(tool, context_channel, value)
    assert not result.ok and not result._implementation_submitted
    assert not submission.reviewer_submission_needs_evidence(tool, result)
    assert not result._review_evidence_incomplete
    assert len(calls) == (1 if failure == "revoked_in_guard" else 0)
    assert store.read(channel.write_key) is None


def test_fake_tool_or_other_channel_cannot_reopen_pending_review():
    _, _, channel, native, note = _tools()
    pending = _execute(note, channel, _legacy_value(_unknown(), channel.write_key))
    forged = replace(note, run=lambda *_: pending)
    assert not submission.reviewer_submission_needs_evidence(forged, pending)
    assert not submission.reviewer_submission_same_channel(forged, native)
    _, _, _, other_native, _ = _tools()
    assert not submission.reviewer_submission_same_channel(note, other_native)
    assert not submission.reviewer_submission_needs_evidence(
        native, ToolResult(ok=True, output='{"status":"pending_review"}'))


def test_invalid_native_declaration_does_not_invoke_guard_or_consume_channel():
    calls = []
    _, store, channel, native, _ = _tools(lambda *args: calls.append(args))
    value = _unknown()
    del value["verdict"]
    result = _execute(native, channel, value)
    assert not result.ok and submission.reviewer_submission_rejected(native, result)
    assert not submission.reviewer_submission_needs_evidence(native, result)
    assert not calls and store.read(channel.write_key) is None


def test_legacy_non_result_key_cannot_trigger_continuation():
    calls = []
    _, store, channel, _, note = _tools(lambda *args: calls.append(args))
    result = _execute(note, channel, _legacy_value(_unknown(), "artifact_unowned"))
    assert not result.ok and not calls
    assert not submission.reviewer_submission_needs_evidence(note, result)
    assert store.read(channel.write_key) is None


def test_absent_guard_preserves_legacy_inconclusive_contract():
    assignment, store, channel, native, note = _tools(None)
    result = _execute(note, channel, _legacy_value(_unknown(), channel.write_key))
    assert result.ok
    assert parse_role_result(assignment, json.loads(store.read(channel.write_key).content))["verdict"] == "inconclusive"
    assert not submission.reviewer_submission_needs_evidence(native, result)


@pytest.mark.parametrize("wrapper", [lambda text: "```json\n" + text + "\n```",
                                    lambda text: "\ufeff" + text])
def test_legacy_format_repair_cannot_hide_an_unfinished_review(wrapper):
    _, store, channel, _, note = _tools()
    value = _legacy_value(_unknown(), channel.write_key)
    value["content"] = wrapper(value["content"])
    result = _execute(note, channel, value)
    assert result.ok, result.output
    assert submission.reviewer_submission_needs_evidence(note, result)
    assert store.read(channel.write_key) is None


@pytest.mark.parametrize("pending", [False, {"detail": "x" * 21_000}])
def test_invalid_host_pending_payload_fails_without_publishing(pending):
    _, store, channel, native, _ = _tools(lambda *_: pending)
    result = _execute(native, channel, _unknown())
    assert not result.ok and not result._implementation_submitted
    assert not submission.reviewer_submission_needs_evidence(native, result)
    assert store.read(channel.write_key) is None


def test_pending_does_not_bypass_existing_approval_coverage_guard():
    assignment, store, channel, _, kwargs = _bound(build=False)
    calls = []

    def coverage_guard(*args):
        calls.append(args)
        raise ValueError("no independent current probe")

    kwargs.update(coverage_guard=coverage_guard,
                  coverage_ref=f"artifact_review_coverage_{assignment.assignment_id}")
    if "continuation_guard" in inspect.signature(submission.create_reviewer_submit_tool).parameters:
        kwargs["continuation_guard"] = _pending_guard
    native = submission.create_reviewer_submit_tool(assignment, channel, **kwargs)
    result = _execute(native, channel, _approval())
    assert not result.ok and submission.reviewer_submission_needs_evidence(native, result)
    assert len(calls) == 1 and store.read(channel.write_key) is None
