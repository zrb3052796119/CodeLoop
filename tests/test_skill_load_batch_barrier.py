from __future__ import annotations

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.skills import discover_skills
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.load_skill import create_load_skill_tool
from minicode.types import AgentStep, ChatMessage, ModelAdapter


class _ScriptedModel(ModelAdapter):
    def __init__(self, steps: list[AgentStep]) -> None:
        self._steps = steps
        self.calls = 0

    def next(
        self,
        messages: list[ChatMessage],
        on_stream_chunk=None,
    ) -> AgentStep:
        step = self._steps[self.calls]
        self.calls += 1
        return step


def _write_skill(root, name: str) -> None:
    target = root / ".mini-code" / "skills" / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {name} instructions",
                "domains: [code]",
                "scopes: [readonly]",
                "tools: [record_action]",
                "supported_operations: [code.explain]",
                "---",
                f"# {name}",
                "",
                f"SECRET-{name.upper()}",
            ]
        ),
        encoding="utf-8",
    )


def _recording_tool(log: list[dict]) -> ToolDefinition:
    def _run(input_data, _context) -> ToolResult:
        log.append(dict(input_data))
        return ToolResult(ok=True, output="recorded")

    return ToolDefinition(
        name="record_action",
        description="Record an action so tests can observe dispatch.",
        input_schema={
            "type": "object",
            "properties": {"note": {"type": "string"}},
        },
        validator=lambda data: data,
        run=_run,
    )


def _run_turn(tmp_path, steps: list[AgentStep]):
    catalog = discover_skills(tmp_path)
    log: list[dict] = []
    tools = ToolRegistry(
        [
            create_load_skill_tool(str(tmp_path), catalog),
            _recording_tool(log),
        ]
    )
    model = _ScriptedModel(steps)
    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "sys"}],
        cwd=str(tmp_path),
    )
    return messages, log, model


def _tool_results(messages) -> dict:
    return {
        message["toolUseId"]: message
        for message in messages
        if message.get("role") == "tool_result"
    }


def test_new_skill_load_defers_other_calls_in_batch(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    steps = [
        AgentStep(
            type="tool_calls",
            calls=[
                {
                    "id": "load",
                    "toolName": "load_skill",
                    "input": {"name": "alpha"},
                },
                {
                    "id": "act",
                    "toolName": "record_action",
                    "input": {"note": "first"},
                },
            ],
        ),
        AgentStep(
            type="tool_calls",
            calls=[
                {
                    "id": "act-2",
                    "toolName": "record_action",
                    "input": {"note": "retry"},
                }
            ],
        ),
        AgentStep(type="assistant", content="done"),
    ]

    messages, log, model = _run_turn(tmp_path, steps)
    results = _tool_results(messages)

    # The load executed; the sibling call was deferred, never dispatched.
    assert results["load"]["isError"] is False
    assert "SECRET-ALPHA" in results["load"]["content"]
    assert results["act"]["isError"] is True
    assert "deferred_until_skill_loaded" in results["act"]["content"]
    assert log == [{"note": "retry"}]

    # Results go back in original call order: load first, deferred second.
    result_ids = [
        message["toolUseId"]
        for message in messages
        if message.get("role") == "tool_result"
    ]
    assert result_ids[:2] == ["load", "act"]

    # The re-proposed call executed normally and the turn completed.
    assert results["act-2"]["isError"] is False
    assert model.calls == len(steps)
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "done"


def test_already_loaded_skill_does_not_trigger_barrier(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    steps = [
        AgentStep(
            type="tool_calls",
            calls=[
                {
                    "id": "load-1",
                    "toolName": "load_skill",
                    "input": {"name": "alpha"},
                }
            ],
        ),
        AgentStep(
            type="tool_calls",
            calls=[
                {
                    "id": "load-2",
                    "toolName": "load_skill",
                    "input": {"name": "alpha"},
                },
                {
                    "id": "act",
                    "toolName": "record_action",
                    "input": {"note": "same-batch"},
                },
            ],
        ),
        AgentStep(type="assistant", content="done"),
    ]

    messages, log, model = _run_turn(tmp_path, steps)
    results = _tool_results(messages)

    # The skill was already recorded as loaded, so the batch runs as today.
    assert results["load-1"]["isError"] is False
    assert results["load-2"]["isError"] is False
    assert results["act"]["isError"] is False
    assert "deferred_until_skill_loaded" not in results["act"]["content"]
    assert log == [{"note": "same-batch"}]
    assert model.calls == len(steps)


def test_load_only_batch_executes_all_loads(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    steps = [
        AgentStep(
            type="tool_calls",
            calls=[
                {
                    "id": "load-a",
                    "toolName": "load_skill",
                    "input": {"name": "alpha"},
                },
                {
                    "id": "load-b",
                    "toolName": "load_skill",
                    "input": {"name": "beta"},
                },
            ],
        ),
        AgentStep(type="assistant", content="done"),
    ]

    messages, log, model = _run_turn(tmp_path, steps)
    results = _tool_results(messages)

    assert results["load-a"]["isError"] is False
    assert "SECRET-ALPHA" in results["load-a"]["content"]
    assert results["load-b"]["isError"] is False
    assert "SECRET-BETA" in results["load-b"]["content"]
    assert log == []
    assert model.calls == len(steps)


def test_multiple_non_load_calls_are_all_deferred(tmp_path) -> None:
    _write_skill(tmp_path, "alpha")
    steps = [
        AgentStep(
            type="tool_calls",
            calls=[
                {
                    "id": "load",
                    "toolName": "load_skill",
                    "input": {"name": "alpha"},
                },
                {
                    "id": "act-x",
                    "toolName": "record_action",
                    "input": {"note": "x"},
                },
                {
                    "id": "act-y",
                    "toolName": "record_action",
                    "input": {"note": "y"},
                },
            ],
        ),
        AgentStep(
            type="tool_calls",
            calls=[
                {
                    "id": "act-x-2",
                    "toolName": "record_action",
                    "input": {"note": "x"},
                },
                {
                    "id": "act-y-2",
                    "toolName": "record_action",
                    "input": {"note": "y"},
                },
            ],
        ),
        AgentStep(type="assistant", content="done"),
    ]

    messages, log, model = _run_turn(tmp_path, steps)
    results = _tool_results(messages)

    # Only the load ran in the first batch; both siblings were deferred.
    assert results["load"]["isError"] is False
    for deferred_id in ("act-x", "act-y"):
        assert results[deferred_id]["isError"] is True
        assert "deferred_until_skill_loaded" in results[deferred_id]["content"]

    # Neither deferred callable was dispatched; re-proposed calls both ran.
    assert log == [{"note": "x"}, {"note": "y"}]
    assert results["act-x-2"]["isError"] is False
    assert results["act-y-2"]["isError"] is False

    # Deferred synthetic errors do not derail the turn: the model was asked
    # for every scripted step and produced its final answer.
    assert model.calls == len(steps)
    assert messages[-1]["content"] == "done"


@pytest.mark.parametrize("bad_input", [["alpha"], "alpha", 42])
def test_malformed_load_is_reported_and_can_be_corrected(tmp_path, bad_input) -> None:
    _write_skill(tmp_path, "alpha")
    steps = [
        AgentStep(type="tool_calls", calls=[
            {"id": "bad-load", "toolName": "load_skill", "input": bad_input},
            {"id": "early", "toolName": "record_action", "input": {"note": "early"}},
        ]),
        AgentStep(type="tool_calls", calls=[
            {"id": "fixed-load", "toolName": "load_skill", "input": {"name": "alpha"}},
        ]),
        AgentStep(type="tool_calls", calls=[
            {"id": "after-body", "toolName": "record_action", "input": {"note": "after"}},
        ]),
        AgentStep(type="assistant", content="done"),
    ]

    messages, log, model = _run_turn(tmp_path, steps)

    results = _tool_results(messages)
    assert results["bad-load"]["isError"] is True
    assert "deferred_until_skill_loaded" in results["early"]["content"]
    assert results["fixed-load"]["isError"] is False
    assert "SECRET-ALPHA" in results["fixed-load"]["content"]
    assert log == [{"note": "after"}]
    assert model.calls == 4
    assert messages[-1]["content"] == "done"
