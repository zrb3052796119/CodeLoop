"""Writing in FINALIZE retains configured capacity within the original ledger.

The model below is a deterministic provider-boundary fixture: it returns only
the source prefix that fits the actual output cap. File edits, pytest, native
submission, and shared-budget admission are production paths. It does not
claim that a live model will use a larger allowance to produce a correct fix.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from copy import deepcopy

import pytest
from test_implementation_finalize_deadline import _submission_tools

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_phases import ImplementationPhaseController
from minicode.permissions import PermissionManager
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget

DOWNSTREAM_FLOOR = 74000


def _budget(*, remaining, max_calls=5):
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    for index in range(10):
        lease = shared.reserve_model_call()
        shared.record_model_call(
            input_tokens=500000 - DOWNSTREAM_FLOOR - remaining if index == 0 else 0,
            reservation=lease,
        )
    worker = _WorkerCallBudget(shared, "output-window", max_calls=max_calls,
                              preserve_calls=10, preserve_tokens=DOWNSTREAM_FLOOR)
    return shared, worker


@pytest.mark.parametrize("remaining,repair,expected_output,expected_future,expected_tail", [
    (176000, True, 8192, 1, 48432),
    (70240, True, 4096, 1, 40240),
    (120000, False, 5452, 2, 90000),
    (31200, True, 1200, 0, 1200),
])
def test_finalize_output_fits_actual_input_and_every_retained_handoff(
    remaining, repair, expected_output, expected_future, expected_tail,
):
    shared, worker = _budget(remaining=remaining)
    controller = ImplementationPhaseController(
        bounded=True, snapshot=worker.snapshot(), max_steps=5, now=0, deadline=1800,
        analysis_complete=True, output_limit=8192, allow_test_completion=True,
        allow_checked_source_completion=True, allow_conditional_handoff=True,
        require_structured_handoff=True,
    )
    controller.observe_verification(failed=repair)
    preview = controller.preview(worker.snapshot(), estimated_input=30000, now=1700)
    request = controller.request(worker.snapshot(), estimated_input=30000, now=1700)
    assert preview == request
    assert not request.handoff_only and controller.allowed_name("edit_file")
    assert request.output_limit == expected_output
    assert request.preserve_calls == expected_future
    assert request.preserve_tokens == expected_tail
    assert request.conditional_handoff is (expected_future == 0)
    assert request.deadline == controller.handoff_start == pytest.approx(1800 - 100 / 3)
    assert controller.deadline == 1800
    before = shared.snapshot()
    lease = worker.reserve_model_call(30000, preserve_model_calls=request.preserve_calls,
                                      preserve_total_tokens=request.preserve_tokens)
    controller.admitted()
    worker.record_model_call(input_tokens=30000, output_tokens=request.output_limit,
                             reservation=lease)
    after = shared.snapshot()
    assert after.used_total_tokens == before.used_total_tokens + 30000 + expected_output
    assert after.used_model_calls == before.used_model_calls + 1
    assert after.reserved_total_tokens == 0
    assert (after.limit_total_tokens, after.limit_model_calls) == (500000, 40)
    assert worker.snapshot().limit_total_tokens == 426000
    assert 500000 - after.used_total_tokens >= DOWNSTREAM_FLOOR


def test_output_quote_preserves_observed_growth_and_configured_lower_limit():
    shared, worker = _budget(remaining=80000)
    controller = ImplementationPhaseController(
        bounded=True, snapshot=worker.snapshot(), max_steps=5, now=0, deadline=1800,
        analysis_complete=True, output_limit=6000, allow_test_completion=True,
        allow_checked_source_completion=True, allow_conditional_handoff=True,
        require_structured_handoff=True,
    )
    controller.observe_verification(failed=True)
    controller.request(worker.snapshot(), estimated_input=20000, now=1)
    request = controller.request(worker.snapshot(), estimated_input=30000, now=1700)
    assert controller._input_growth == 10000
    assert request.output_limit == 6000
    # Preserve this response, the future 30000+10000 input, and 2048 closing output.
    assert request.preserve_calls == 1 and request.preserve_tokens == 48048
    worker.reserve_model_call(30000, preserve_model_calls=1, preserve_total_tokens=48048)
    assert shared.snapshot().limit_total_tokens == 500000


def test_concurrent_reservation_is_not_borrowed_to_raise_the_output_cap():
    shared, worker = _budget(remaining=75240)
    concurrent = shared.reserve_model_call(5000)
    controller = ImplementationPhaseController(
        bounded=True, snapshot=worker.snapshot(), max_steps=5, now=0, deadline=1800,
        analysis_complete=True, output_limit=8192, allow_test_completion=True,
        allow_checked_source_completion=True, allow_conditional_handoff=True,
        require_structured_handoff=True,
    )
    controller.observe_verification(failed=True)
    request = controller.request(worker.snapshot(), estimated_input=30000, now=1700)
    assert request.output_limit == 4096 and request.preserve_tokens == 40240
    assert request.preserve_calls == 1 and not request.conditional_handoff
    before = shared.snapshot()
    # Simply replacing the old cap with 8192 would deny an otherwise funded
    # repair. Its larger output/replay reserve cannot consume another lease.
    with pytest.raises(AgentBudgetExceeded):
        worker.reserve_model_call(30000, preserve_model_calls=1, preserve_total_tokens=48432)
    assert shared.snapshot() == before
    lease = worker.reserve_model_call(30000, preserve_model_calls=1,
                                      preserve_total_tokens=request.preserve_tokens)
    worker.record_model_call(input_tokens=30000, output_tokens=request.output_limit,
                             reservation=lease)
    assert shared.snapshot().reserved_total_tokens == 5000
    shared.record_model_call(input_tokens=5000, reservation=concurrent)
    assert shared.snapshot().reserved_total_tokens == 0
    assert worker.snapshot().limit_total_tokens == 426000


def test_submit_only_keeps_original_cap_and_cannot_reopen_writes():
    _, worker = _budget(remaining=176000)
    controller = ImplementationPhaseController(
        bounded=True, snapshot=worker.snapshot(), max_steps=5, now=0, deadline=1800,
        analysis_complete=True, output_limit=8192, allow_test_completion=True,
        allow_checked_source_completion=True, allow_conditional_handoff=True,
        require_structured_handoff=True,
    )
    controller.observe_verification(failed=True)
    controller.request(worker.snapshot(), estimated_input=30000, now=1700)
    boundary = controller.handoff_start
    request = controller.request(worker.snapshot(), estimated_input=30000, now=boundary)
    assert request.handoff_only and not request.conditional_handoff
    assert request.output_limit == request.preserve_tokens == 2048
    assert request.preserve_calls == 0 and request.deadline == 1800
    assert not controller.allowed_name("edit_file")
    controller.admitted()
    controller.observe_handoff_result(accepted=False)
    retry = controller.request(worker.snapshot(), estimated_input=100, now=boundary + 1)
    assert retry.handoff_only and retry.output_limit == 2048
    assert not controller.allowed_name("executor_test")


def test_input_cannot_borrow_the_output_or_downstream_floor():
    shared, worker = _budget(remaining=29999)
    controller = ImplementationPhaseController(
        bounded=True, snapshot=worker.snapshot(), max_steps=5, now=0, deadline=1800,
        analysis_complete=True, output_limit=8192, allow_test_completion=True,
        allow_checked_source_completion=True, allow_conditional_handoff=True,
        require_structured_handoff=True,
    )
    controller.observe_verification(failed=True)
    request = controller.request(worker.snapshot(), estimated_input=30000, now=1700)
    assert request.conditional_handoff and request.output_limit == 1
    before = shared.snapshot()
    with pytest.raises(AgentBudgetExceeded):
        worker.reserve_model_call(30000, preserve_model_calls=request.preserve_calls,
                                  preserve_total_tokens=request.preserve_tokens)
    assert shared.snapshot() == before


@pytest.mark.parametrize("conditional", [False, True])
def test_real_loop_does_not_truncate_a_funded_final_source_response(
    tmp_path, conditional,
):
    original = "def value():\n    return 0\n"
    full_source = ("VALUES = {\n"
                   + "".join(f'    "case_{index:04d}": {index},\n' for index in range(850))
                   + '}\n\ndef value():\n    return VALUES["case_0002"]\n')
    target = tmp_path / "candidate.py"
    target.write_text(original)
    (tmp_path / "test_candidate.py").write_text(
        "from candidate import value\n\ndef test_value():\n    assert value() == 2\n"
    )
    assignment, _, store, channel, submit = _submission_tools(tmp_path)
    checks, caps, bills, views = [], [], [], []
    shared, worker = _budget(remaining=176000, max_calls=2 if conditional else 3)
    owner = time.monotonic() + 1800

    def digest(_context):
        return hashlib.sha256(target.read_bytes()).hexdigest()

    def check(item, context):
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "test_candidate.py"],
            cwd=context.cwd, capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "PYTHONPATH": str(tmp_path),
                 "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""},
        )
        checks.append(completed.returncode)
        return {"checkId": item.check_id, "status": "passed" if completed.returncode == 0 else "failed",
                "patchDigest": digest(context), "stdout": completed.stdout, "stderr": completed.stderr}

    cap = ExecutorCheckCapability(
        (VerificationCheck("public", "tests", ("pytest", "-q", "test_candidate.py"), 30),),
        check, test_paths=("test_candidate.py",), current_patch_digest=digest,
    )
    registry = cap.bind_registry(ToolRegistry([edit_file_tool, submit]))
    declaration = {"id": "submit", "toolName": "executor_submit", "input": {
        "status": "completed", "steps": [{"step": 1, "status": "completed"}],
        "deviations": [], "blockers": [],
    }}
    edit = {"id": "write-table", "toolName": "edit_file", "input": {
        "path": "candidate.py", "old": original, "new": full_source,
    }}
    planned = [edit, declaration] if conditional else [edit]
    full_output = estimate_tokens(json.dumps(planned))
    assert 4096 < full_output < 8192

    class Model:
        def next(self, messages, *, tools_override, max_output_tokens_override, **_kwargs):
            caps.append(max_output_tokens_override)
            names = {tool.name for tool in tools_override.list()}
            views.append(names)
            if len(caps) == 1:
                calls = [{"id": "initial-check", "toolName": "executor_check", "input": {"check": 1}}]
            elif len(caps) == 2:
                assert "edit_file" in names
                calls = deepcopy(planned)
                # A controlled response respects the cap actually supplied by
                # the production loop, without inventing extra output budget.
                low, high = 0, len(full_source)
                while low < high:
                    middle = (low + high + 1) // 2
                    calls[0]["input"]["new"] = full_source[:middle]
                    if estimate_tokens(json.dumps(calls)) <= max_output_tokens_override:
                        low = middle
                    else:
                        high = middle - 1
                calls[0]["input"]["new"] = full_source[:low]
            else:
                assert not conditional and len(caps) == 3
                calls = [deepcopy(declaration)]
            output = estimate_tokens(json.dumps(calls))
            assert output <= max_output_tokens_override
            schemas = [{"name": tool.name, "parameters": tool.input_schema}
                       for tool in tools_override.list()]
            input_tokens = sum(estimate_message_tokens(message) for message in messages) + estimate_tokens(json.dumps(schemas))
            bills.append(input_tokens + output)
            return AgentStep(type="tool_calls", calls=calls,
                             usage=ModelUsage(input_tokens=input_tokens, output_tokens=output, source="estimated"))

    try:
        transcript = run_agent_turn(
            bounded_implementation_phases=True, model=Model(), tools=registry,
            messages=[{"role": "user", "content": "Implement the supplied lookup table and verify value returns 2."}],
            cwd=str(tmp_path), runtime={"maxOutputTokens": 8192},
            permissions=PermissionManager(str(tmp_path), prompt=lambda _: {"decision": "allow_once"}),
            agent_budget=worker, deadline_monotonic=owner, subagent_mailbox=channel,
            implementation_task=True, implementation_analysis_complete=True,
            max_steps=2 if conditional else 3, agent_depth=1, enable_work_chain=False,
        )
        assert target.read_text() == full_source, ("funded write was truncated by host output cap", caps)
        assert checks == [1, 0]
        assert caps[1] == 8192
        if not conditional:
            assert caps[2] == 2048 and views[2] == {"executor_submit"}
        declaration_artifact = json.loads(store.read(channel.write_key).content)
        assert declaration_artifact["status"] == "completed"
        assert declaration_artifact["assignmentId"] == assignment.assignment_id
        assert any(message.get("toolUseId") == "submit" and message.get("role") == "tool_result"
                   and not message.get("isError") for message in transcript)
        assert shared.snapshot().used_total_tokens == 250000 + sum(bills)
        assert shared.snapshot().used_model_calls == 10 + len(caps)
        assert (shared.snapshot().limit_total_tokens, shared.snapshot().limit_model_calls) == (500000, 40)
        assert shared.snapshot().reserved_total_tokens == 0
        assert 500000 - shared.snapshot().used_total_tokens >= DOWNSTREAM_FLOOR
        assert worker.snapshot().limit_total_tokens == 426000
    finally:
        cap.close()
        channel.close()
