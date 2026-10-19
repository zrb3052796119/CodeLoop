"""The actual sent request must include the mode selected after notice pricing.

This is an Agent Loop admission regression, not a live-model completion test.
The phase starts with a host-observed failure; the only real tool action is an
honest blocked declaration, so no check result or successful repair is invented.
"""

import json
import time
from copy import deepcopy

from test_implementation_finalize_deadline import _submission_tools

from minicode.agent_budget import AgentTurnBudget
from minicode.context_manager import estimate_message_tokens
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_phases import ImplementationPhaseController
from minicode.task_ledger import TaskLedger
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelUsage


def test_added_phase_notice_reprices_and_reaches_the_actual_model(tmp_path, monkeypatch):
    import minicode.agent_loop as loop

    original = [{"role": "user", "content": "Repair the assigned public test. " + "Relevant context. " * 3000}]
    initial_messages = TaskLedger.from_messages(original).reconcile(original)
    initial_input = sum(estimate_message_tokens(message) for message in initial_messages)
    # At the initial input, repair + handoff fit with 100 tokens to spare.
    # The genuine phase notice then consumes that margin and selects the
    # one-response conditional path. The extra notice must also be priced.
    total = 2 * initial_input + 4096 + 2048 + 4096 + 100
    reservations, previews, controllers, sent = [], [], [], []

    class RecordingBudget(AgentTurnBudget):
        def reserve_model_call(self, estimated_input_tokens=0, **kwargs):
            reservations.append((estimated_input_tokens, dict(kwargs)))
            return super().reserve_model_call(estimated_input_tokens, **kwargs)

    class ObservedPhase(ImplementationPhaseController):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.observe_verification(failed=True)
            controllers.append(self)

        def preview(self, snapshot, *, estimated_input, now):
            request = super().preview(snapshot, estimated_input=estimated_input, now=now)
            previews.append((estimated_input, request.conditional_handoff, request.preserve_calls))
            return request

    monkeypatch.setattr(loop, "ImplementationPhaseController", ObservedPhase)
    budget = RecordingBudget(max_model_calls=40, max_total_tokens=total)
    _assignment, _order, store, channel, submit = _submission_tools(tmp_path)

    def unexpected_check(*_args):
        raise AssertionError("A blocked declaration does not run or invent a check.")

    capability = ExecutorCheckCapability(
        (VerificationCheck("public", "tests", ("python", "-m", "pytest"), 30),),
        unexpected_check, test_paths=("test_candidate.py",),
        current_patch_digest=lambda _context: "sha256:" + "a" * 64,
    )
    tools = capability.bind_registry(ToolRegistry([edit_file_tool, submit]))
    assert capability.supports_conditional_submit

    class Model:
        def next(self, messages, tools_override=None, **_kwargs):
            sent.append(deepcopy(messages))
            assert len(sent) == 1
            assert "executor_test" in {tool.name for tool in tools_override.list()}
            return AgentStep(type="tool_calls", calls=[{
                "id": "honest-blocker", "toolName": "executor_submit", "input": {
                    "status": "blocked", "steps": [{"step": 1, "status": "failed"}],
                    "deviations": [], "blockers": ["Required repair evidence is unavailable."],
                },
            }], usage=ModelUsage(input_tokens=sum(estimate_message_tokens(m) for m in messages),
                                 output_tokens=50, source="estimated"))

    try:
        canonical = loop.run_agent_turn(model=Model(), tools=tools, messages=original,
            cwd=str(tmp_path), agent_budget=budget, subagent_mailbox=channel,
            implementation_task=True, implementation_analysis_complete=True,
            max_steps=15, agent_depth=1, deadline_monotonic=time.monotonic() + 600,
            enable_work_chain=False)
        assert len(sent) == len(controllers) == len(reservations) == 1
        assert any(not conditional and future == 1 for _price, conditional, future in previews)
        assert any(conditional and future == 0 for _price, conditional, future in previews)
        mode_messages = [message for message in sent[0]
            if message.get("_implementation_phase") and "Host conditional handoff:" in message["content"]]
        assert len(mode_messages) == 1, "The final selected mode was logged but not delivered to the model."
        assert mode_messages[0] in canonical
        actual_input = sum(estimate_message_tokens(message) for message in sent[0])
        assert reservations == [(actual_input, {"preserve_model_calls": 0, "preserve_total_tokens": 8192})]
        assert controllers[0]._previous_input == actual_input
        assert controllers[0].calls == 1 and controllers[0].conditional_handoff
        assert budget.snapshot().used_model_calls == 1
        assert budget.snapshot().used_total_tokens == actual_input + 50
        assert budget.snapshot().reserved_total_tokens == 0
        assert budget.snapshot().limit_total_tokens == total
        assert json.loads(store.read(channel.write_key).content)["status"] == "blocked"
    finally:
        capability.close()
        channel.close()
