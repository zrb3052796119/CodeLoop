from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import minicode.tools.task as task_module
from minicode.run_journal import RunJournal
from minicode.subagent_mailbox import SubagentMailbox
from minicode.subagent_result import (
    extract_subagent_result,
    project_subagent_result,
    render_subagent_result,
)
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext
from minicode.tools.task import MAX_AGENT_DEPTH


def _reported_turn(**kwargs):
    prompt = "\n".join(str(message.get("content", "")) for message in kwargs["messages"])
    match = re.search(r"result mailbox key `([^`]+)`", prompt)
    assert match is not None
    kwargs["subagent_mailbox"].write(
        match.group(1),
        json.dumps(
            {
                "resultVersion": 1,
                "summary": "Inspected and verified the requested scope.",
                "files": [{"path": "minicode/tools/task.py", "action": "read"}],
                "risks": ["A legacy caller may omit the prompt."],
                "verification": {
                    "status": "passed",
                    "checks": ["pytest tests/test_subagent_structured_protocol.py"],
                },
            }
        ),
        author="1",
    )
    kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
    return [{"role": "assistant", "content": "human hand-back"}]


@pytest.mark.parametrize("agent_type", ["explore", "plan", "general"])
def test_every_direct_task_type_returns_the_same_typed_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_type: str,
) -> None:
    monkeypatch.setattr(task_module, "run_agent_turn", _reported_turn)
    mailbox = SubagentMailbox()

    result = task_module.task_tool.run(
        {
            "description": "typed handback",
            "prompt": "Inspect the task implementation.",
            "agent_type": agent_type,
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _subagent_mailbox=mailbox,
        ),
    )

    structured = extract_subagent_result(result.output)
    assert result.ok is True
    assert structured is not None
    assert structured["agentType"] == agent_type
    assert structured["contractStatus"] == "reported"
    assert structured["files"] == [
        {"path": "minicode/tools/task.py", "action": "read"}
    ]
    assert structured["verification"]["status"] == "passed"


def test_task_result_parent_event_and_sidecar_join_on_subagent_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(task_module, "run_agent_turn", _reported_turn)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = run_journal.create_run(title="parent", source="headless")
    run_journal.transition(record.id, "running")
    sidecar = run_journal.open_subagent_journal(record.id)

    class Sink:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def emit(self, event_type, *, step=None, payload=None) -> None:
            if event_type == "subagent.completed":
                event = run_journal.append_event(
                    record.id,
                    event_type,
                    step=step,
                    payload=payload,
                )
                self.payloads.append(event.payload)

        def open_subagent_journal(self):
            return sidecar

    sink = Sink()
    result = task_module.task_tool.run(
        {
            "description": "join evidence",
            "prompt": "Inspect the task implementation.",
            "agent_type": "explore",
        },
        ToolContext(
            cwd=str(workspace),
            _runtime={"model": "fake"},
            _subagent_mailbox=SubagentMailbox(),
            _event_sink=sink,
        ),
    )

    structured = extract_subagent_result(result.output)
    summaries = run_journal.list_subagent_runs(record.id)
    assert structured is not None
    assert len(sink.payloads) == 1
    assert len(summaries) == 1
    assert sink.payloads[0]["subagentId"] == structured["subagentId"]
    assert summaries[0].subagent_id == structured["subagentId"]


def test_missing_typed_report_is_visible_as_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreported_turn(**kwargs):
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "plain result"}]

    monkeypatch.setattr(task_module, "run_agent_turn", unreported_turn)
    result = task_module.task_tool.run(
        {
            "description": "fallback result",
            "prompt": "Inspect the task implementation.",
            "agent_type": "explore",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _subagent_mailbox=SubagentMailbox(),
        ),
    )

    structured = extract_subagent_result(result.output)
    assert structured is not None
    assert structured["contractStatus"] == "fallback"
    assert structured["summary"] == "plain result"
    assert structured["verification"] == {
        "status": "inconclusive",
        "checks": [],
    }


def test_workflow_depth_rejection_uses_the_unified_observation(
    tmp_path: Path,
) -> None:
    payloads: list[dict[str, object]] = []

    class Sink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            if event_type == "subagent.completed":
                payloads.append(payload)

    result = task_module.task_tool.run(
        {
            "description": "nested workflow",
            "prompt": "Do nested work.",
            "agent_type": "workflow",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _agent_depth=MAX_AGENT_DEPTH,
            _event_sink=Sink(),
        ),
    )

    assert result.ok is False
    assert "sub_agent_depth_exceeded" in result.output
    assert payloads[0]["subagentVersion"] == 3
    assert payloads[0]["agentType"] == "workflow"
    assert payloads[0]["phaseCount"] == 0
    assert payloads[0]["resultContractStatus"] == "unavailable"


def test_workflow_aggregates_phase_contracts_into_the_same_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = run_journal.create_run(title="workflow parent", source="headless")
    run_journal.transition(record.id, "running")
    sidecar = run_journal.open_subagent_journal(record.id)
    mailbox = SubagentMailbox()
    payloads: list[dict[str, object]] = []
    calls = 0

    class Sink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            if event_type == "subagent.completed":
                event = run_journal.append_event(
                    record.id,
                    event_type,
                    step=step,
                    payload=payload,
                )
                payloads.append(event.payload)

        def open_subagent_journal(self):
            return sidecar

    def fake_run(input_data: dict, _context) -> object:
        nonlocal calls
        calls += 1
        agent_type = input_data["agent_type"]
        if input_data["description"].startswith("review:"):
            match = re.search(r"review verdict key `([^`]+)`", input_data["prompt"])
            assert match is not None
            mailbox.write(
                match.group(1),
                json.dumps(
                    {
                        "reviewVersion": 1,
                        "verdict": "approved",
                        "blockingFindings": [],
                        "warnings": ["Monitor the legacy caller."],
                    }
                ),
                author="reviewer",
            )
        report = json.dumps(
            {
                "resultVersion": 1,
                "summary": f"{agent_type} phase complete",
                "files": [
                    {
                        "path": "minicode/tools/task.py",
                        "action": "modified" if agent_type == "general" else "read",
                    }
                ],
                "risks": [f"{agent_type} phase risk"],
                "verification": {
                    "status": "passed" if agent_type == "general" else "not_run",
                    "checks": ["pytest focused"] if agent_type == "general" else [],
                },
            }
        )
        structured = project_subagent_result(
            report,
            subagent_id="sub_" + f"{calls:032x}",
            agent_type=agent_type,
            outcome="completed",
            fallback_summary="unused",
        )
        from minicode.tooling import ToolResult

        return ToolResult(
            ok=True,
            output=f"phase narrative\n\n{render_subagent_result(structured)}",
        )

    monkeypatch.setattr(task_module, "_run", fake_run)
    result = task_module.task_tool.run(
        {
            "description": "workflow contract",
            "prompt": "Implement and verify the task protocol.",
            "agent_type": "workflow",
        },
        ToolContext(
            cwd=str(workspace),
            _runtime={"model": "fake"},
            _subagent_mailbox=mailbox,
            _event_sink=Sink(),
        ),
    )

    structured = extract_subagent_result(result.output)
    assert result.ok is True
    assert structured is not None
    assert structured["agentType"] == "workflow"
    assert structured["contractStatus"] == "derived"
    assert {item["action"] for item in structured["files"]} == {"read", "modified"}
    assert "Monitor the legacy caller." in structured["risks"]
    assert structured["verification"] == {
        "status": "passed",
        "checks": ["workflow review verdict: approved"],
    }
    assert payloads[0]["subagentId"] == structured["subagentId"]
    summaries = run_journal.list_subagent_runs(record.id)
    assert len(summaries) == 1
    assert summaries[0].agent_type == "workflow"
    assert summaries[0].limit_kind == "phases"
    assert summaries[0].subagent_id == structured["subagentId"]
