"""Continue real execution after a rejected candidate in the same assignment.

The scripted adapter controls decisions; it is not a model-quality benchmark.
The workflow, task worker, agent loop, edits, pytest, snapshots and promotion
all run their real production paths. Model/network, registry bootstrap and the
Reviewer verdict are fixtures; the Reviewer model-to-Executor repair path is
covered by test_workflow_completion_coverage. The recorded V17 failure shape is a small provenance fixture;
the local failing command is executed, never replaced by that recorded output.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from minicode import subagent_model_routing
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolRegistry, ToolResult
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelAdapter
import minicode.workflow_runner as runner_module

from test_workflow_v2_structured_routing import _request, _run, _write_reviewer_result


FIXTURE = json.loads(
    Path(__file__).with_name("fixtures").joinpath(
        "v17-executor-failure-feedback.json"
    ).read_text(encoding="utf-8")
)
INITIAL = "def outcome():\n    return 'before'\n"
GOOD = "def outcome():\n    return 'after'\n"
FINDING = "The implementation entry point is outcome; retain this investigation."


def _exercise(tmp_path, monkeypatch, *, large_diff=False, repair=True, legacy_incomplete_evidence=False, on_demand=False):
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL, encoding="utf-8")
    (tmp_path / "test_acceptance.py").write_text(
        "from candidate import outcome\n"
        "def test_outcome():\n"
        "    assert outcome() == 'after'\n",
        encoding="utf-8",
    )
    # V17's missing TaskTool caused a real runtime NameError. Extra source
    # lines reproduce its truncated *display* diff without weakening checks.
    bad = "def outcome():\n    return TaskTool().run()\n"
    padding = "".join(f"# candidate context line {i:04d} " + "x" * 70 + "\n"
                      for i in range(300 if legacy_incomplete_evidence else 180))
    if legacy_incomplete_evidence:
        import minicode.workflow_workspace as workspace_module

        original_diff = workspace_module._frozen_diff_document

        def legacy_preview(*, patch, base_digest, patch_digest):
            document = original_diff(patch=patch, base_digest=base_digest, patch_digest=patch_digest)
            if len(patch) > 12_000:
                # Simulate a stored legacy preview, retaining the real complete
                # patch's digest/count. The final gate must still reject it.
                document = {**document, "complete": False, "encoding": "utf-8",
                            "diff": (patch[:6_000] + b"\n...<truncated>...\n" + patch[-6_000:]).decode("utf-8")}
            return document

        monkeypatch.setattr(workspace_module, "_frozen_diff_document", legacy_preview)
    if large_diff:
        bad += padding
    calls = []
    commands = []
    workers = []

    def command_runner(_argv, **kwargs):
        entry = {"cwd": kwargs["cwd"], "parent": target.read_text(encoding="utf-8")}
        completed = subprocess.run([sys.executable, "-m", "pytest", "-q"], **kwargs)
        output = completed.stdout.decode("utf-8", errors="replace") if isinstance(completed.stdout, bytes) else completed.stdout
        entry.update(exit_code=completed.returncode, stdout=output)
        commands.append(entry)
        return completed

    class ScriptedModel(ModelAdapter):
        def next(self, messages, **_kwargs):
            calls.append(deepcopy(messages))
            number = len(calls)
            if number == 3:
                rejection = next(m for m in messages if m.get("role") == "tool_result"
                                 and m.get("toolUseId") == "submit-2")
                assert rejection["isError"], "The known failed check must reject completed immediately."
                assert "completed" in rejection["content"]
                if not repair:
                    return AgentStep(type="tool_calls", calls=[{
                        "id": "blocked-3", "toolName": "executor_submit", "input": {
                            "status": "blocked", "steps": [{"step": 1, "status": "failed"}],
                            "deviations": [], "blockers": ["The candidate still raises NameError in pytest."],
                        },
                    }])
            if number in {1, 3}:
                replacement = bad if number == 1 else (
                    GOOD + (padding if legacy_incomplete_evidence else "")
                )
                return AgentStep(
                    type="tool_calls", content=FINDING if number == 1 else "Repair the rejected candidate.",
                    calls=[{"id": f"edit-{number}", "toolName": "edit_file", "input": {
                        "path": "candidate.py", "old": INITIAL if number == 1 else bad,
                        "new": replacement,
                    }}],
                )
            if number in {2, 4}:
                declaration = deepcopy(FIXTURE["submitInput"])
                declaration["steps"] = [{"step": 1, "status": "completed"}]
                return AgentStep(type="tool_calls", calls=[{
                    "id": f"submit-{number}", "toolName": "executor_submit", "input": declaration,
                }])
            raise AssertionError("Unexpected additional scripted model request")

    model = ScriptedModel()
    original_worker = task_module._run

    def worker(input_data, context):
        workers.append((context._logical_agent_role, context._agent_budget,
                        input_data.get("_executor_session")))
        # These model calls are the Executor's complete, persistent history.
        # Use a separate valid Reviewer fixture instead of spending its calls
        # on an adapter that intentionally understands only Executor actions.
        if context._logical_agent_role == "reviewer":
            assert target.read_text(encoding="utf-8") == INITIAL
            _write_reviewer_result(input_data, context)
            return ToolResult(ok=True, output="Reviewed the repaired candidate.")
        return original_worker(input_data, context)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", lambda *_a, **_k: model)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([edit_file_tool]))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a: None)
    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command_runner))
    request = _request(risk="low", complex=False)
    request["scope"]["writePaths"] = ["candidate.py"]
    if on_demand:
        request["verificationChecks"][0]["argv"] = ["pytest", "-q", "test_acceptance.py"]
    data_dir = tmp_path.parent / f"{tmp_path.name}-run-journal"
    with observe_run(
        workspace=tmp_path, source="headless", title="persistent Executor local replay",
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ) as observation:
        if on_demand:
            from test_workflow_on_demand_review import _run_request
            result = _run_request(tmp_path, request, event_sink=observation,
                user_request="Set the outcome to after.\nAcceptance command: `pytest -q test_acceptance.py`")
        else:
            result = _run(tmp_path, request, monkeypatch, event_sink=observation, central_enforced=True)
    # The gate feedback stays in one Assignment and one Session. The final
    # Reviewer still uses the workflow's existing ledger, not a fresh grant.
    executors = [entry for entry in workers if entry[0] == "executor"]
    assert len(executors) == 1 and executors[0][2] is not None
    budgets = []
    for _role, budget, _session in workers:
        while hasattr(budget, "_budget"):
            budget = budget._budget
        budgets.append(budget)
    assert all(budget is budgets[0] for budget in budgets)
    assert budgets[0].snapshot().used_model_calls == len(calls) + (2 if on_demand else 0)
    assert budgets[0].snapshot().reserved_total_tokens == 0
    if result.ok:
        assert [role for role, _budget, _session in workers] == (["executor"] if on_demand else ["executor", "reviewer"])
    return result, calls, commands, target


@pytest.mark.parametrize("large_diff", [False, True], ids=["small-diff", "v17-truncated-display-diff"])
def test_failed_check_gate_keeps_executor_conversation_and_candidate(
    tmp_path, monkeypatch, large_diff,
):
    result, calls, commands, target = _exercise(tmp_path, monkeypatch, large_diff=large_diff)
    assert len(calls) == 4, "A rejected candidate must return to its executor for repair"
    assert result.ok, result.output
    assert target.read_text(encoding="utf-8") == GOOD
    assert [c["exit_code"] for c in commands] == [1, 0, 0], (
        "A failed write-time test must reach the same Executor; the repaired candidate gets a fresh formal test"
    )
    resumed = calls[2]
    assert sum(m.get("_implementation_context") is True for m in calls[0]) == 1
    assert sum(m.get("_implementation_context") is True for m in resumed) == 1, (
        "Repair must retain its existing source packet without injecting it a second time"
    )
    prior_calls = [m for m in resumed if m.get("role") == "assistant_tool_call"]
    assert any(m.get("toolUseId") == "edit-1" for m in prior_calls), (
        "The check-gate repair discarded the Executor's earlier tool-call history"
    )
    assert any(FINDING in m.get("content", "") for m in resumed
               if m.get("role") in {"assistant", "assistant_progress", "assistant_tool_call"}), (
        "The Executor's completed investigation must remain in its conversation"
    )
    prior_results = [m for m in resumed if m.get("role") == "tool_result"]
    assert any(m.get("toolUseId") == "edit-1" and FIXTURE["failedCheck"]["error"] in m.get("content", "")
               for m in prior_results), "Original write/test evidence must survive the gate rejection"
    rejected = next(m for m in prior_results if m.get("toolUseId") == "submit-2")
    assert rejected["isError"] and "completed" in rejected["content"]
    assert "IMPLEMENTATION REPAIR REQUEST" not in str(resumed), "This is same-assignment feedback."
    assert all(c["parent"] == INITIAL for c in commands), "No writeback before the final check passes"
    assert len({c["cwd"] for c in commands}) == 3
    trace = extract_scheduler_trace(result.output)
    assert trace is not None and trace["committed"] is True
    assert trace["status"] == "completed"
    assert {t["stage"] for t in trace["transitions"] if t["outcome"] == "completed"} >= {
        "executor", "verifier", "reviewer"}


def test_completed_declaration_cannot_promote_a_still_failing_candidate(tmp_path, monkeypatch):
    result, calls, commands, target = _exercise(tmp_path, monkeypatch, repair=False)
    assert len(calls) == 3
    assert not result.ok
    assert target.read_text(encoding="utf-8") == INITIAL
    assert [c["exit_code"] for c in commands] == [1]
    assert all(FIXTURE["failedCheck"]["error"] in c["stdout"] for c in commands)
    trace = extract_scheduler_trace(result.output)
    assert trace is not None and trace["committed"] is False


def test_repaired_candidate_with_legacy_incomplete_evidence_cannot_commit(tmp_path, monkeypatch):
    result, calls, commands, target = _exercise(
        tmp_path, monkeypatch, legacy_incomplete_evidence=True,
    )
    assert len(calls) == 4
    assert [c["exit_code"] for c in commands] == [1, 0, 0]
    assert not result.ok, "Passing tests cannot replace the final gate's complete patch evidence"
    assert target.read_text(encoding="utf-8") == INITIAL
    trace = extract_scheduler_trace(result.output)
    assert trace is not None and trace["committed"] is False


def test_user_bound_acceptance_preserves_one_executor_through_failed_check_repair(tmp_path, monkeypatch):
    result, calls, commands, target = _exercise(tmp_path, monkeypatch, on_demand=True)
    assert result.ok, result.output
    assert len(calls) == 4
    assert target.read_text() == GOOD
    assert [item["exit_code"] for item in commands] == [1, 0, 0]
    assert extract_scheduler_trace(result.output)["ruleId"] == "route.verified_change"
    assert result._workflow_delivery_receipt is not None
