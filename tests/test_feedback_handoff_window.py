"""Late real edits remain testable inside the original owner deadline."""

import json
import subprocess
import sys
from types import SimpleNamespace

from minicode.agent_budget import AgentTurnBudget
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_phases import ImplementationPhaseController
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool


def test_late_edit_runs_actual_check_and_retains_handoff_time(tmp_path, monkeypatch):
    target = tmp_path / "value.py"
    target.write_text("value = 1\n")
    clock = SimpleNamespace(now=490.0)
    monkeypatch.setattr("time.monotonic", lambda: clock.now)
    seen = []

    def check(_check, context):
        seen.append(context._task_deadline_monotonic)
        result = subprocess.run([sys.executable, "-c",
            "from pathlib import Path; assert Path('value.py').read_text() == 'value = 2\\n'"],
            cwd=context.cwd, capture_output=True, text=True, timeout=5)
        return {"checkId": "public", "status": "passed" if result.returncode == 0 else "failed",
                "exitCode": result.returncode, "stderr": result.stderr}

    cap = ExecutorCheckCapability((VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_value.py"), 5),), check)
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    phases = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=30,
        now=66.0, deadline=520.0, allow_checked_source_completion=True,
        allow_test_completion=True, require_structured_handoff=True)
    phases.observe_verification(failed=True)
    phases.request(budget.snapshot(), estimated_input=1000, now=469.0)
    cap.reserve_handoff_time(phases.handoff_start)
    registry = cap.bind_registry(ToolRegistry([edit_file_tool]))
    context = ToolContext(cwd=str(tmp_path), _task_deadline_monotonic=520.0)
    result = registry.execute("edit_file", {"path": "value.py", "old": "value = 1", "new": "value = 2"}, context)
    assert result.ok and result._file_changed
    assert target.read_text() == "value = 2\n"
    assert seen, "a valid late edit was never checked because an old phase floor had expired"
    assert seen == [503.0], "the check must respect the committed 17-second handoff window"
    assert result._implementation_checks_failed is False
    clock.now = 521.0
    late = registry.execute("executor_check", {"check": 1}, context)
    assert not late.ok and len(seen) == 1, "no test may start after the hard owner deadline"
    assert json.loads(late.output)["status"] == "inconclusive"


def test_feedback_cannot_extend_a_tighter_caller_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 490.0)
    seen = []

    def check(_check, context):
        seen.append(context._task_deadline_monotonic)
        return {"checkId": "public", "status": "passed"}

    cap = ExecutorCheckCapability((VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_value.py"), 5),), check)
    cap.reserve_handoff_time(555.0)
    result = cap.tool.run({"check": 1}, ToolContext(cwd=str(tmp_path), _task_deadline_monotonic=500.0))
    assert result.ok and seen == [500.0]


def test_explicit_absolute_feedback_deadline_still_applies(tmp_path, monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 490.0)
    calls = []
    cap = ExecutorCheckCapability((VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_value.py"), 5),),
                                  lambda *_: calls.append(1))
    cap.reserve_handoff_time(480.0)
    result = cap.tool.run({"check": 1}, ToolContext(cwd=str(tmp_path), _task_deadline_monotonic=600.0))
    assert not result.ok and not calls
