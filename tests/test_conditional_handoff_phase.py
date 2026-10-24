"""A checked final batch uses one admission without renewing work authority."""

from types import SimpleNamespace

from minicode.implementation_phases import ImplementationPhaseController


def policy(*, checked=True):
    snapshot = SimpleNamespace(limit_model_calls=40, used_model_calls=23,
        limit_total_tokens=482583, used_total_tokens=441311, reserved_total_tokens=0)
    controller = ImplementationPhaseController(snapshot=snapshot, max_steps=15,
        now=300, deadline=550, analysis_complete=True,
        allow_test_completion=True, allow_checked_source_completion=True,
        allow_conditional_handoff=checked, require_structured_handoff=True, bounded=True)
    controller.observe_verification(failed=True)
    return controller, snapshot


def test_last_checked_repair_prices_one_response_with_original_output_limit():
    controller, snapshot = policy()
    request = controller.request(snapshot, estimated_input=30000, now=320)
    assert request.conditional_handoff and not request.handoff_only
    assert request.preserve_calls == 0
    assert request.preserve_tokens == request.output_limit == 8192
    assert request.deadline <= controller.handoff_start
    assert controller.allowed_name("edit_file")
    assert controller.allowed_name("executor_test")
    assert not controller.allowed_name("read_file")
    assert not controller.allowed_name("task")
    assert controller.call_limit == 15
    calls = [dict(id="repair", toolName="edit_file", input={}),
             dict(id="finish", toolName="executor_submit", input={})]
    assert controller.admit_batch(calls) == {}


def test_unbound_check_gate_retains_the_original_submit_only_boundary():
    controller, snapshot = policy(checked=False)
    request = controller.request(snapshot, estimated_input=30000, now=320)
    assert request.handoff_only and not request.conditional_handoff
    assert not controller.allowed_name("edit_file")


def test_fixed_work_deadline_and_admitted_handoff_never_reopen_repair():
    controller, snapshot = policy()
    first = controller.request(snapshot, estimated_input=30000, now=320)
    assert first.conditional_handoff
    boundary = controller.handoff_start
    final = controller.request(snapshot, estimated_input=30000, now=boundary)
    assert final.handoff_only and not final.conditional_handoff
    controller.admitted()
    controller.observe_handoff_result(accepted=False)
    controller.observe_verification(failed=True)
    # Even a different later estimate cannot undo the admitted handoff lock.
    retry = controller.request(snapshot, estimated_input=1000, now=boundary + 1)
    assert retry.handoff_only and not retry.conditional_handoff
    assert controller.handoff_start == boundary
    assert not controller.allowed_name("executor_test")


def test_zero_call_allowance_cannot_offer_conditional_repair():
    controller, snapshot = policy()
    controller.calls = controller.call_limit
    request = controller.request(snapshot, estimated_input=30000, now=320)
    assert controller.exhausted
    assert not request.conditional_handoff
    assert not controller.allowed_name("edit_file")


def test_conditional_submit_before_writes_rejects_the_whole_batch():
    controller, snapshot = policy()
    controller.request(snapshot, estimated_input=30000, now=320)
    calls = [dict(id="finish", toolName="executor_submit", input={}),
             dict(id="repair", toolName="edit_file", input={})]
    rejected = controller.admit_batch(calls)
    assert set(rejected) == {"finish", "repair"}
    assert all("implementation_handoff_order" in text for text in rejected.values())
