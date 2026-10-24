"""Large phase prose remains complete beyond inline and subprocess limits."""

import hashlib
import json
from pathlib import Path

import pytest

from minicode import subagent_worker, workflow_brief
from minicode.agent_budget import AgentTurnBudget
from minicode.subagent_mailbox import SubagentMailbox
from minicode.subagent_supervisor import SubagentProcessOutcome
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext
from minicode.tools import task as task_module
from minicode.workspace import resolve_tool_path
from test_subagent_subprocess import _TYPED_REPORT, _run_worker_in_process


@pytest.mark.parametrize("successful", [True, False])
@pytest.mark.parametrize("size", [70_000, 300_000])
def test_workflow_phase_returns_complete_body_before_snapshot_archive(
    tmp_path, monkeypatch, successful, size,
):
    body = "BEGIN完整\n" + "x" * size + "\nEND完整"
    detail = "DETAIL\n" + "d" * 7000 + "\nDETAIL END"

    def turn(**kwargs):
        if successful:
            kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
            kwargs["subagent_mailbox"].write(
                "transport_result", json.dumps(_TYPED_REPORT), author="child",
            )
            kwargs["subagent_mailbox"].write(
                "transport_result_detail", detail, author="child",
            )
        return [{"role": "assistant", "content": body}]

    monkeypatch.setattr(task_module, "run_agent_turn", turn)
    result = task_module.task_tool.run(
        {"agent_type": "explore", "description": "retain phase output",
         "prompt": "Inspect code.", "_result_key": "transport_result",
         "_result_instruction": "Return the complete findings."},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"},
                    _workflow_phase=True, _subagent_mailbox=SubagentMailbox()),
    )
    assert result.ok is successful
    assert body in result.output
    if successful:
        assert detail in result.output
    assert not (tmp_path / ".workflow-briefs").exists()


@pytest.mark.parametrize("successful", [True, False])
@pytest.mark.parametrize("size", [70_000, 300_000])
def test_subprocess_body_uses_existing_string_reference_with_exact_file(
    tmp_path, monkeypatch, successful, size,
):
    body = "BEGIN完整\n" + "x" * size + "\nEND完整"

    def turn(**kwargs):
        if successful:
            kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": body}]

    monkeypatch.setattr(subagent_worker, "run_agent_turn", turn)
    code, payload = _run_worker_in_process(tmp_path)
    assert code == (0 if successful else 1)
    assert payload["status"] == ("completed" if successful else "failed")
    assert len(payload["final_message"]) <= 8000
    assert len(json.dumps(payload).encode()) < 64_000
    digest = hashlib.sha256(body.encode()).hexdigest()
    reference = f".workflow-briefs/{digest}.md"
    assert reference in payload["final_message"]
    assert f"SHA-256: {digest}" in payload["final_message"]

    monkeypatch.setattr(task_module, "run_subagent_process", lambda *_a, **_k:
        SubagentProcessOutcome(status=payload["status"], result=payload,
                               error=None, duration_ms=1, pid=None))
    context = ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"},
                          _agent_budget=AgentTurnBudget())
    result = task_module.task_tool.run(
        {"agent_type": "general", "description": "retain child output",
         "prompt": "Update code."}, context,
    )
    assert result.ok is successful
    assert reference in result.output
    assert resolve_tool_path(context, reference, "read").read_bytes() == body.encode()


def test_subprocess_archive_failure_is_explicit_instead_of_silent_data_loss(
    tmp_path, monkeypatch,
):
    def turn(**kwargs):
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "x" * 300_000}]

    def deny_archive(*_args, **_kwargs):
        raise PermissionError("output archive denied")

    monkeypatch.setattr(subagent_worker, "run_agent_turn", turn)
    monkeypatch.setattr(workflow_brief, "write_workflow_artifact", deny_archive)
    code, payload = _run_worker_in_process(tmp_path)
    assert code == 1
    assert payload["status"] == "error"
    assert "output archive denied" in payload["error"]
    assert payload["final_message"] == ""
    assert not list(Path(tmp_path).glob(".workflow-briefs/*.md"))


@pytest.mark.parametrize("size", [4001, 8000])
def test_subprocess_typed_failure_retains_complete_small_inline_body(tmp_path, monkeypatch, size):
    body = "x" * (size - 8) + "END_BODY"
    monkeypatch.setattr(subagent_worker, "run_agent_turn", lambda **_kwargs:
                        [{"role": "assistant", "content": body}])
    code, payload = _run_worker_in_process(tmp_path)
    assert code == 1 and payload["final_message"] == body
    monkeypatch.setattr(task_module, "run_subagent_process", lambda *_a, **_k:
        SubagentProcessOutcome(status="failed", result=payload, error=None, duration_ms=1, pid=None))
    result = task_module.task_tool.run(
        {"agent_type": "general", "description": "retain failure", "prompt": "Update code."},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}, _agent_budget=AgentTurnBudget()),
    )
    assert not result.ok and body in result.output
    assert not (tmp_path / ".workflow-briefs").exists()
