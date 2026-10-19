"""Optional custom-profile limits reach the real read-only task loop unchanged."""

import json
import re

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.subagent_mailbox import SubagentMailbox
from minicode.subagent_profiles import load_agent_profiles
from minicode.subagent_result import extract_subagent_result
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools import task as task_module
from minicode.tools.read_file import read_file_tool
from minicode.tools.subagent_notes import subagent_note_write_tool
from minicode.types import AgentStep


def _profile(root, turns):
    directory = root / ".mini-code" / "agents"
    directory.mkdir(parents=True)
    (directory / "test-locator.md").write_text(
        "---\nname: test-locator\ndescription: Locate the relevant file.\n"
        + (f"max_turns: {turns}\n" if turns is not None else "")
        + "allowed_tools: read_file\n---\nRead the requested file and report its path.\n"
    )


@pytest.mark.parametrize(("raw", "expected"), [
    (None, None), ("0", None), ("1", 1), ("8", 8), ("200", 200),
    ("201", 201), ("500", 500),
])
def test_profile_optional_turn_limit_has_no_implicit_ceiling(tmp_path, raw, expected):
    _profile(tmp_path, raw)
    profiles, errors = load_agent_profiles(tmp_path, user_root=tmp_path / "home")
    assert errors == []
    assert profiles["test-locator"]["max_turns"] == expected
    assert profiles["test-locator"]["allowed_tools"] == frozenset({"read_file"})


@pytest.mark.parametrize("raw", ["", "-1", "eight", "3.5", "true", "false", "null", "NaN", "[]"])
def test_explicit_malformed_profile_turn_limit_still_rejects_the_profile(tmp_path, raw):
    _profile(tmp_path, raw)
    profiles, errors = load_agent_profiles(tmp_path, user_root=tmp_path / "home")
    assert profiles == {} and len(errors) == 1
    assert "max_turns" in errors[0]


@pytest.mark.parametrize(("raw", "expected"), [(None, None), ("0", None), ("500", 500)])
def test_optional_custom_limit_survives_real_task_loop_and_journal(tmp_path, monkeypatch, raw, expected):
    _profile(tmp_path, raw)
    (tmp_path / "evidence.txt").write_text("The requested file exists.\n")
    recorded = []
    original_loop = task_module.run_agent_turn
    budget = AgentTurnBudget()

    def observe_loop(**kwargs):
        recorded.append(kwargs)
        assert kwargs["max_steps"] == expected
        assert kwargs["deadline_monotonic"] is None
        names = {tool.name for tool in kwargs["tools"].list()}
        assert names == {"read_file", "subagent_note_write"}
        return original_loop(**kwargs)

    class Model:
        calls = 0

        def next(self, messages, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return AgentStep(type="tool_calls", calls=[{
                    "id": "read-evidence", "toolName": "read_file", "input": {"path": "evidence.txt"},
                }])
            if self.calls == 2:
                assert any(message.get("toolUseId") == "read-evidence"
                           and message.get("role") == "tool_result" and not message.get("isError")
                           and "The requested file exists." in message.get("content", "")
                           for message in messages)
                key = re.search(r"mailbox key `([^`]+)`", messages[0]["content"]).group(1)
                return AgentStep(type="tool_calls", calls=[{
                    "id": "publish-note", "toolName": "subagent_note_write", "input": {"key": key,
                    "content": json.dumps({"resultVersion": 1, "summary": "Located evidence.txt.",
                        "files": [{"path": "evidence.txt", "action": "read"}], "risks": [],
                        "verification": {"status": "not_run", "checks": []}})},
                }])
            assert self.calls == 3
            assert any(message.get("toolUseId") == "publish-note"
                       and message.get("role") == "tool_result" and not message.get("isError")
                       for message in messages)
            return AgentStep(type="assistant", kind="final", content="Located evidence.txt.")

    model = Model()
    monkeypatch.setattr(task_module, "run_agent_turn", observe_loop)
    monkeypatch.setattr(task_module, "_build_sub_agent_system_prompt", lambda **kw: (kw["agent_def"]["system_prompt"], None))
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", lambda *_a, **_kw: model)
    monkeypatch.setattr("minicode.intent_semantic_classifier.build_semantic_intent_adapter", lambda *_a, **_kw: None)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_kw: ToolRegistry([read_file_tool, subagent_note_write_tool]))
    monkeypatch.delenv("MINICODE_TASK_TIMEOUT_SECONDS", raising=False)
    data_dir = tmp_path / "journal-data"
    with observe_run(workspace=tmp_path, source="headless", title="optional custom limit",
                     journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run(
            {"description": "locate a file", "prompt": "Read evidence.txt and report its path.", "agent_type": "test-locator"},
            ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture"}, _agent_budget=budget,
                        _event_sink=observation, _subagent_mailbox=SubagentMailbox()),
        )
        run_id = observation.run_id
    assert result.ok, result.output
    assert extract_subagent_result(result.output)["contractStatus"] == "reported"
    assert len(recorded) == 1 and model.calls == 3
    assert budget.snapshot().used_model_calls == 3
    assert budget.snapshot().limit_model_calls is None
    journal = RunJournal(tmp_path, data_dir=data_dir)
    children = journal.list_subagent_runs(run_id)
    assert len(children) == 1 and children[0].outcome == "completed"
    assert children[0].max_turns == expected
    assert (tmp_path / "evidence.txt").read_text() == "The requested file exists.\n"
