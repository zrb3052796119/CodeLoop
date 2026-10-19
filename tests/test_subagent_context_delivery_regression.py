"""Completed survey context survives bounded generic result remediation."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

import minicode.tools.task as task_module
from minicode.subagent_mailbox import SubagentMailbox
from minicode.subagent_result import extract_subagent_result
from minicode.subagent_sessions import SubagentSessionStore
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext

_FINDING = "billing/invoices.py:47 creates invoice records."


def _report(summary: str = _FINDING) -> str:
    return json.dumps({
        "resultVersion": 1, "summary": summary,
        "files": [{"path": "billing/invoices.py", "action": "read"}],
        "risks": [], "verification": {"status": "not_run", "checks": []},
    })


def _write_report(kwargs, content: str) -> None:
    prompt = "\n".join(str(message.get("content", "")) for message in kwargs["messages"])
    key = re.search(r"mailbox key `(subagent_result_[a-z0-9_]+)`", prompt).group(1)
    kwargs["subagent_mailbox"].write(key, content, author="explore")


def _successful(kwargs) -> None:
    kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))


def _task(tmp_path, *, store=None):
    return ToolContext(
        cwd=str(tmp_path), _runtime={"model": "fake"},
        _subagent_mailbox=SubagentMailbox(), _subagent_sessions=store,
    )


def test_reminder_and_continuation_keep_completed_tool_evidence(monkeypatch, tmp_path):
    calls = []
    store = SubagentSessionStore()

    def run_turn(**kwargs):
        calls.append(kwargs)
        _successful(kwargs)
        if len(calls) == 1:
            return [
                *kwargs["messages"],
                {"role": "tool_result", "toolUseId": "read-1", "toolName": "read_file", "content": _FINDING},
                {"role": "assistant", "content": "<final> " + _FINDING},
            ]
        assert any(message.get("role") == "tool_result" and message.get("content") == _FINDING for message in kwargs["messages"])
        assert any(message.get("role") == "assistant" and _FINDING in message.get("content", "") for message in kwargs["messages"])
        _write_report(kwargs, _report())
        return [*kwargs["messages"], {"role": "assistant", "content": "<final> note submitted"}]

    monkeypatch.setattr(task_module, "run_agent_turn", run_turn)
    result = task_module.task_tool.run(
        {"description": "map billing", "prompt": "Map billing ownership.", "agent_type": "explore"},
        _task(tmp_path, store=store),
    )
    assert result.ok, result.output
    assert len(calls) == 2
    assert "recovered via closing reminder" in result.output
    assert "<final> " + _FINDING in result.output
    assert "\n<final> note submitted\n" not in result.output
    session_id = re.search(r"continue_subagent_id='(sub_[0-9a-f]{32})'", result.output).group(1)
    saved = store.get(session_id)
    assert any(message.get("role") == "tool_result" and message.get("content") == _FINDING for message in saved.messages)


@pytest.mark.parametrize("initial", ["not JSON", _report().replace('"resultVersion": 1', '"resultVersion": 2')])
def test_malformed_existing_note_gets_one_validated_correction(monkeypatch, tmp_path, initial):
    calls = []

    def run_turn(**kwargs):
        calls.append(kwargs)
        _successful(kwargs)
        _write_report(kwargs, initial if len(calls) == 1 else _report())
        return [*kwargs["messages"], {"role": "assistant", "content": "<final> " + _FINDING}]

    monkeypatch.setattr(task_module, "run_agent_turn", run_turn)
    result = task_module.task_tool.run(
        {"description": "map billing", "prompt": "Map billing ownership.", "agent_type": "explore"}, _task(tmp_path)
    )
    assert result.ok, result.output
    assert len(calls) == 2
    assert extract_subagent_result(result.output)["contractStatus"] == "reported"
    assert "recovered via closing reminder" in result.output


def test_invalid_reminder_note_is_not_marked_recovered(monkeypatch, tmp_path):
    calls = []

    def run_turn(**kwargs):
        calls.append(kwargs)
        _successful(kwargs)
        if len(calls) == 2:
            _write_report(kwargs, "malformed reminder report")
        return [*kwargs["messages"], {"role": "assistant", "content": "<final> " + _FINDING}]

    monkeypatch.setattr(task_module, "run_agent_turn", run_turn)
    result = task_module.task_tool.run(
        {"description": "map billing", "prompt": "Map billing ownership.", "agent_type": "explore"}, _task(tmp_path)
    )
    assert result.ok, result.output
    assert len(calls) == 2
    assert extract_subagent_result(result.output)["contractStatus"] == "fallback"
    assert "recovered via closing reminder" not in result.output


@pytest.mark.parametrize("reminder", ["valid", "refused", "raises"])
def test_final_telemetry_counts_all_reminder_attempts(monkeypatch, tmp_path, reminder):
    calls = []
    observed = []
    finished = []
    journal = SimpleNamespace(
        start=lambda **kwargs: None,
        append_event=lambda *args, **kwargs: None,
        finish=lambda *args, **kwargs: finished.append(kwargs),
    )
    monkeypatch.setattr(task_module, "_open_subagent_journal", lambda context: journal)
    monkeypatch.setattr(task_module, "_emit_subagent_observation", lambda context, **kwargs: observed.append(kwargs))

    def run_turn(**kwargs):
        calls.append(kwargs)
        kwargs["event_sink"].emit("model.started", step=1)
        kwargs["event_sink"].emit("model.started", step=2)
        _successful(kwargs)
        if len(calls) == 2:
            kwargs["event_sink"].emit("tool.started", step=1)
            if reminder == "raises":
                raise ConnectionError("closing call failed")
            if reminder == "valid":
                _write_report(kwargs, _report())
        return [*kwargs["messages"], {"role": "assistant", "content": "<final> " + _FINDING}]

    monkeypatch.setattr(task_module, "run_agent_turn", run_turn)
    result = task_module.task_tool.run(
        {"description": "map billing", "prompt": "Map billing ownership.", "agent_type": "explore"}, _task(tmp_path)
    )
    assert result.ok, result.output
    assert "Model turns: 4 (tool calls: 1)" in result.output
    assert observed[-1]["model_turns"] == finished[-1]["model_turns"] == 4
    assert observed[-1]["tool_calls"] == finished[-1]["tool_calls"] == 1


@pytest.mark.parametrize("role", [None, "analyst"])
def test_strict_role_instructions_do_not_receive_generic_remediation(monkeypatch, tmp_path, role):
    calls = []

    def run_turn(**kwargs):
        calls.append(kwargs)
        _successful(kwargs)
        return [*kwargs["messages"], {"role": "assistant", "content": "<final> strict role completed"}]

    monkeypatch.setattr(task_module, "run_agent_turn", run_turn)
    context = _task(tmp_path)
    input_data = {"description": "strict report", "prompt": "Investigate the immutable assignment.", "agent_type": "plan"}
    if role is None:
        input_data["_result_instruction"] = "Publish only CustomRoleReport.v1."
    else:
        context._logical_agent_role = role
        context._workflow_phase = True
        context._logical_agent_scope = {"read_roots": (".",), "write_paths": (), "forbidden_paths": ()}
        context._logical_agent_workspace_root = str(tmp_path)
    result = task_module._run(input_data, context)
    assert result.ok, result.output
    assert len(calls) == 1
    assert "closing reminder" not in result.output
