"""Existing Executor artifact authority can recover whole long probe sources."""

from dataclasses import replace
import hashlib
import json
import threading
import time

import pytest

from minicode.executor_result_submission import ExecutorResultChannel
from minicode.multi_agent_contracts import parse_assignment, parse_implementation_order
from minicode.review_probe_source import review_probe_source_artifact_id
from minicode.reviewer_verification import ReviewerProbe
from minicode.scheduler_artifacts import SchedulerArtifactStore
from minicode.subagent_mailbox import SubagentMailbox
from minicode.task_deadline import TaskDeadlineExceeded
from minicode.tooling import ToolContext, ToolExecutionAbandoned, ToolRegistry
from minicode.tools.subagent_notes import subagent_note_read_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from test_multi_agent_v2_execution_contracts import _executor_assignment, _implementation_order


def _executor():
    assignment = parse_assignment(_executor_assignment())
    order = parse_implementation_order(assignment, _implementation_order())
    store = SchedulerArtifactStore(scheduler_authority=object())
    code = "# 公共源🙂\n" * 10000 + "def test_requirement():\n    assert 1 == 2\n"
    probe = ReviewerProbe.create(criterion_ids=["ac_behavior"], scenario="public source recovery", code=code)
    key = review_probe_source_artifact_id(probe.probe_id)
    artifact = store.publish(key, probe.to_dict(), authority=store._scheduler_authority)
    inner = store.create_assignment_channel(readable_keys=(key,), write_key="artifact_executor_result")
    channel = ExecutorResultChannel(inner, assignment, order)
    context = ToolContext(_subagent_mailbox=channel, _logical_agent_role="executor")
    return store, channel, context, key, artifact.content, code


def _read(context, value):
    return ToolRegistry([subagent_note_read_tool]).execute("subagent_note_read", value, context)


def test_existing_executor_source_authority_auto_pages_and_rebuilds_exact_unicode():
    store, _channel, context, key, content, code = _executor()
    assert len(content) > 50000
    first = _read(context, {"key": key})
    assert first.ok
    assert first.output.startswith("{"), "Large authorized notes need a usable page, not head/tail truncation."
    page = json.loads(first.output)
    parts, pages = [], 0
    while True:
        assert page["key"] == key and page["totalChars"] == len(content)
        assert page["digest"] == "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        assert page["offset"] == sum(map(len, parts))
        assert len(page["text"]) <= 4000
        parts.append(page["text"])
        pages += 1
        if page["nextOffset"] is None:
            break
        result = _read(context, {"key": key, "offset": page["nextOffset"], "limit": 137})
        assert result.ok
        assert len(result.output) < 30000
        page = json.loads(result.output)
    assert pages > 100 and "".join(parts) == content
    assert json.loads("".join(parts))["code"] == code
    assert store.read("artifact_executor_result") is None


def test_small_shared_note_default_is_unchanged_and_explicit_paging_is_exact():
    mailbox = SubagentMailbox()
    mailbox.write("handoff", "测试🙂\nnext", author="executor")
    context = ToolContext(_subagent_mailbox=mailbox)
    original = _read(context, {"key": "handoff"})
    assert original.ok and original.output == "[note handoff v1 by executor]\n测试🙂\nnext"
    result = _read(context, {"key": "handoff", "offset": 1, "limit": 2})
    assert result.ok and result.output.startswith("{"), "Explicit paging must not be silently ignored."
    page = json.loads(result.output)
    assert page["text"] == "试🙂" and page["offset"] == 1 and page["nextOffset"] == 3
    mailbox.write("handoff", "updated", author="executor")
    updated = json.loads(_read(context, {"key": "handoff", "offset": 1, "limit": 2}).output)
    assert updated["version"] == 2 and updated["digest"] != page["digest"]


@pytest.mark.parametrize("state", ["closed", "revoked", "other-owner", "missing-owner"])
def test_pages_cannot_reuse_closed_or_other_assignment_authority(state):
    store, channel, context, key, _content, _code = _executor()
    if state == "closed":
        channel.close()
        assert channel.read(key) is not None, "The tool must guard the existing host-only read behavior."
    elif state == "revoked":
        store.revoke_pending_assignment_writes(authority=store._scheduler_authority)
    elif state == "other-owner":
        other = store.create_assignment_channel(readable_keys=(), write_key="artifact_other_result")
        context = replace(context, _subagent_mailbox=other)
    else:
        context = replace(context, _subagent_mailbox=None)
    result = _read(context, {"key": key, "offset": 0, "limit": 100})
    assert not result.ok and "公共" not in result.output


@pytest.mark.parametrize("value", [
    {"offset": -1}, {"offset": True}, {"offset": None},
    {"limit": 0}, {"limit": 4001}, {"limit": False}, {"artifactPath": "/private/unknown"},
])
def test_invalid_page_fields_are_not_silently_dropped(value):
    mailbox = SubagentMailbox()
    mailbox.write("allowed", "visible only through authorized read", author="host")
    result = _read(ToolContext(_subagent_mailbox=mailbox), {"key": "allowed", **value})
    assert not result.ok


@pytest.mark.parametrize("control", ["cancel", "deadline", "abandoned"])
def test_reader_stops_before_mailbox_read_when_owner_is_stopped(control):
    class ObservedMailbox(SubagentMailbox):
        reads = 0

        def read(self, key):
            self.reads += 1
            return super().read(key)

    mailbox = ObservedMailbox()
    mailbox.write("allowed", "public", author="host")
    context = ToolContext(_subagent_mailbox=mailbox)
    if control == "cancel":
        token = TurnCancellationToken("turn_" + "e" * 32)
        token.request()
        context = replace(context, _cancellation_token=token)
        expected = TurnCancellationRequested
    elif control == "deadline":
        context = replace(context, _task_deadline_monotonic=time.monotonic() - 1)
        expected = TaskDeadlineExceeded
    else:
        event = threading.Event()
        event.set()
        context = replace(context, _tool_abandoned=event)
        expected = ToolExecutionAbandoned
    with pytest.raises(expected):
        subagent_note_read_tool.run({"key": "allowed", "offset": 0}, context)
    assert mailbox.reads == 0
