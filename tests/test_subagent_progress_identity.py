"""The chat stream identifies workflow roles and concurrent worker instances."""

import json
from types import SimpleNamespace

from minicode.tools.task import _sub_agent_progress_callbacks
from minicode.web.chat_stream import ChatStreamWriter


def test_stream_distinguishes_logical_roles_and_same_type_instances():
    frames = []
    stream = ChatStreamWriter("turn_" + "1" * 32, frames.append)
    context = SimpleNamespace(_presentation=stream)
    for logical_role, run_id in [
        ("analyst", "sub_" + "1" * 32),
        ("reviewer", "sub_" + "2" * 32),
        ("adjudicator", "sub_" + "3" * 32),
        (None, "sub_" + "4" * 32),
        (None, "sub_" + "5" * 32),
    ]:
        agent_type = "plan" if logical_role else "explore"
        started, finished = _sub_agent_progress_callbacks(
            context, agent_type, subagent_id=run_id, logical_role=logical_role,
        )
        started("read_file", {"path": "private-task-data"})
        finished("read_file", "private-output", False)
    decoded = [json.loads(frame) for frame in frames]
    assert [frame["toolName"] for frame in decoded[::2]] == [
        "analyst:11111111▸read_file", "reviewer:22222222▸read_file",
        "adjudicator:33333333▸read_file", "explore:44444444▸read_file",
        "explore:55555555▸read_file",
    ]
    for start, finish in zip(decoded[::2], decoded[1::2]):
        assert start["toolName"] == finish["toolName"]
        assert start["toolStreamId"] == finish["toolStreamId"]
        assert finish["paired"] is True
    assert not stream.detached
    assert "private" not in b"".join(frames).decode()
