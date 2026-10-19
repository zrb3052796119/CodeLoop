"""An unavailable host check capability is partial, never an endless repair."""

import subprocess
import sys

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_execution import ImplementationSpec
from minicode.permissions import PermissionManager
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep


@pytest.mark.parametrize("configured_check", [False, True])
def test_no_runnable_completion_capability_stops_partial_with_candidate_retained(
        tmp_path, monkeypatch, configured_check):
    (tmp_path / "app.py").write_text("answer = 42\n")
    (tmp_path / "test_app.py").write_text("from app import answer\ndef test_answer():\n    assert answer == 42\n")
    checks = ((VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_app.py"), 20),)
              if configured_check else ())
    commands = []

    def run(argv, **kwargs):
        commands.append(list(argv))
        return subprocess.run([sys.executable, *argv[1:]], **kwargs)

    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=run))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)

    class Executor:
        calls = 0

        def next(self, _messages, **_kwargs):
            self.calls += 1
            assert self.calls == 1, "missing host authority cannot be repaired by repeating the same final"
            return AgentStep(type="assistant", kind="final", content="All requested behavior is complete.")

    model, outcome, presented = Executor(), AgentOutcomeCapture(), []
    result = run_agent_turn(model=model, tools=ToolRegistry([edit_file_tool]),
        messages=[{"role": "user", "content": "Implement the answer."}], cwd=str(tmp_path),
        implementation_task=True, implementation_spec=ImplementationSpec(("app.py",), checks, "Implement the answer."),
        max_steps=None, enable_work_chain=False, outcome_capture=outcome,
        on_assistant_message=presented.append)
    assert model.calls == 1
    assert len(commands) == int(configured_check)
    assert (tmp_path / "app.py").read_text() == "answer = 42\n"
    assert not outcome.outcome.completion_succeeded
    assert not outcome.outcome.goal_achieved
    assert "All requested behavior is complete." not in presented
    assert result[-1]["_host_implementation_receipt"] and result[-1]["_partial"]
