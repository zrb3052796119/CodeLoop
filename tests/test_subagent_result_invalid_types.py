"""Malformed model fields fall back without turning completed work into errors."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

import minicode.tools.task as task_module
from minicode.subagent_mailbox import SubagentMailbox
from minicode.subagent_result import extract_subagent_result, project_subagent_result
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext


def _report(field: str, invalid: object) -> str:
    report = {
        "resultVersion": 1, "summary": "Completed investigation.",
        "files": [{"path": "auth.py", "action": "read"}], "risks": [],
        "verification": {"status": "not_run", "checks": []},
    }
    if field == "action":
        report["files"][0]["action"] = invalid
    else:
        report["verification"]["status"] = invalid
    return json.dumps(report)


@pytest.mark.parametrize("field", ["action", "status"])
@pytest.mark.parametrize("invalid", [["read"], {"value": "read"}])
def test_unhashable_report_fields_produce_explicit_evidence_free_fallback(
    field: str, invalid: object,
) -> None:
    projected = project_subagent_result(
        _report(field, invalid), subagent_id="sub_" + "1" * 32,
        agent_type="explore", outcome="completed", fallback_summary="auth.py owns login.",
    )
    assert projected["contractStatus"] == "fallback"
    assert projected["summary"] == "auth.py owns login."
    assert projected["files"] == []
    assert projected["verification"]["status"] == "inconclusive"


def test_completed_task_with_malformed_report_preserves_success_and_findings(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(task_module, "_build_sub_agent_system_prompt", lambda **kw: (kw["agent_def"]["system_prompt"], None))
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", lambda *a, **kw: SimpleNamespace(runtime={"model": "fake"}))
    monkeypatch.setattr("minicode.intent_semantic_classifier.build_semantic_intent_adapter", lambda *a, **kw: None)

    def finished(**kwargs):
        key = re.search(r"mailbox key `([^`]+)`", kwargs["messages"][0]["content"]).group(1)
        kwargs["subagent_mailbox"].write(key, _report("action", ["read"]), author="explore")
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [*kwargs["messages"], {"role": "assistant", "content": "<final> auth.py owns login."}]

    monkeypatch.setattr(task_module, "run_agent_turn", finished)
    result = task_module.task_tool.run(
        {"description": "auth", "prompt": "Find login code.", "agent_type": "explore"},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}, _subagent_mailbox=SubagentMailbox()),
    )
    assert result.ok, result.output
    report = extract_subagent_result(result.output)
    assert report is not None and report["contractStatus"] == "fallback"
    assert "auth.py owns login" in report["summary"]
