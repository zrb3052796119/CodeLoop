"""Current candidate receipts gate native declarations, never final promotion."""

import hashlib
import json
import threading
import time
from dataclasses import replace

import pytest

from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.tooling import ToolContext, ToolRegistry, ToolResult
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.subagent_notes import subagent_note_write_tool
from test_executor_result_submission import _bound, _submission


def _gated(tmp_path, *, with_digest=True, writer=edit_file_tool):
    assignment, order, store, channel, submit = _bound()
    target = tmp_path / "app.py"
    target.write_text("value = 1\n")
    observed = {"a": "passed", "b": "passed", "runs": [], "digest_calls": 0}

    def digest(_context):
        observed["digest_calls"] += 1
        return hashlib.sha256(target.read_bytes()).hexdigest()

    def check(item, context):
        observed["runs"].append(item.check_id)
        return {"checkId": item.check_id, "status": observed[item.check_id],
                "patchDigest": observed.get("digest_" + item.check_id, digest(context))}

    checks = tuple(VerificationCheck(name, "tests", ("pytest", name), 1) for name in ("a", "b"))
    cap = ExecutorCheckCapability(checks, check, current_patch_digest=digest if with_digest else None)
    registry = cap.bind_registry(ToolRegistry([submit, writer, subagent_note_write_tool]))
    context = ToolContext(cwd=str(tmp_path), _subagent_mailbox=channel)
    return cap, registry, context, observed, store, channel, target, assignment, order


def _check_all(registry, context):
    return [registry.execute("executor_check", {"check": number}, context) for number in (1, 2)]


def _submit(registry, context):
    return registry.execute("executor_submit", _submission(), context)


def _assert_unwritten(store, channel, result):
    assert not result.ok, result.output
    assert not result._implementation_submitted
    assert store.read(channel.write_key) is None


def test_all_approved_checks_pass_current_digest_before_native_handoff(tmp_path):
    cap, registry, context, state, store, channel, *_ = _gated(tmp_path)
    assert cap.supports_conditional_submit
    assert all(result.ok for result in _check_all(registry, context))
    before = len(state["runs"])
    result = _submit(registry, context)
    assert result.ok and result._implementation_submitted, result.output
    assert len(state["runs"]) == before, "submission validates digests without rerunning checks"
    assert json.loads(store.read(channel.write_key).content)["status"] == "completed"
    assert json.loads(result.output)["verification"] == "pending"


def test_completion_pending_only_reads_current_host_receipt_state(tmp_path):
    cap, registry, context, state, _, _, *_ = _gated(tmp_path)
    assert cap.completion_pending
    assert registry.execute("executor_check", {"check": 1}, context).ok
    assert cap.completion_pending
    assert registry.execute("executor_check", {"check": 2}, context).ok
    calls = state["digest_calls"]
    assert not cap.completion_pending
    assert state["digest_calls"] == calls, "a phase preview cannot freeze or query candidate bytes"
    state["digest_b"] = "different"
    assert registry.execute("executor_check", {"check": 2}, context).ok
    assert cap.completion_pending
    del state["digest_b"]
    assert registry.execute("executor_check", {"check": 2}, context).ok
    assert not cap.completion_pending
    cap.observe_tool_result("edit_file", {"path": "app.py"}, ToolResult(ok=False, output="rejected"), context=context)
    assert cap.completion_pending
    cap.begin_model_response(context=context)
    assert cap.completion_pending, "acknowledging an error cannot restore its invalidated receipts"
    cap.close()
    assert not cap.completion_pending and not cap.supports_conditional_submit


@pytest.mark.parametrize("second", ["missing", "failed", "inconclusive"])
def test_single_pass_cannot_erase_another_missing_or_unpassed_check(tmp_path, second):
    _, registry, context, state, store, channel, *_ = _gated(tmp_path)
    if second != "missing":
        state["b"] = second
        registry.execute("executor_check", {"check": 2}, context)
    assert registry.execute("executor_check", {"check": 1}, context).ok
    _assert_unwritten(store, channel, _submit(registry, context))


@pytest.mark.parametrize("digest", [None, "", "another-candidate"])
def test_missing_or_mismatched_receipt_digest_cannot_submit(tmp_path, digest):
    _, registry, context, state, store, channel, *_ = _gated(tmp_path)
    state["digest_b"] = digest
    _check_all(registry, context)
    _assert_unwritten(store, channel, _submit(registry, context))


def test_out_of_band_change_is_detected_by_current_candidate_digest(tmp_path):
    _, registry, context, _, store, channel, target, *_ = _gated(tmp_path)
    _check_all(registry, context)
    target.write_text("value = 2\n")
    result = _submit(registry, context)
    _assert_unwritten(store, channel, result)
    assert "stale" in result.output


def test_changed_write_invalidates_until_serial_batch_checks_flush(tmp_path):
    cap, registry, context, state, store, channel, target, *_ = _gated(tmp_path)
    _check_all(registry, context)
    batch = cap.begin_write_batch()
    batch.call_id = "repair-1"
    assert registry.execute("edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 2"}, context).ok
    _assert_unwritten(store, channel, _submit(registry, context))
    batch.call_id = "repair-2"
    assert registry.execute("edit_file", {"path": "app.py", "old": "value = 2", "new": "value = 3"}, context).ok
    assert state["runs"] == ["a", "b"], "consecutive writes defer checks until final bytes"
    document, error = batch.finish()
    assert error is None and document["status"] == "passed"
    assert document["changedCallIds"] == ["repair-1", "repair-2"]
    assert target.read_text() == "value = 3\n"
    assert state["runs"] == ["a", "b", "a", "b"]
    assert _submit(registry, context).ok


def test_no_change_retains_receipts_without_rerunning_checks(tmp_path):
    _, registry, context, state, _, _, *_ = _gated(tmp_path)
    _check_all(registry, context)
    result = registry.execute("edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 1"}, context)
    assert result.ok and not result._file_changed
    assert state["runs"] == ["a", "b"]
    assert _submit(registry, context).ok


def test_last_failed_write_cannot_be_hidden_by_passing_batch_checks(tmp_path):
    cap, registry, context, _, store, channel, target, *_ = _gated(tmp_path)
    batch = cap.begin_write_batch()
    batch.call_id = "actual-change"
    assert registry.execute("edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 2"}, context).ok
    batch.call_id = "failed-final-edit"
    failed = registry.execute("edit_file", {"path": "app.py", "old": "absent anchor", "new": "value = 3"}, context)
    assert not failed.ok
    receipt, error = batch.finish()
    assert error is None and receipt["status"] == "passed"
    assert target.read_text() == "value = 2\n"
    result = _submit(registry, context)
    _assert_unwritten(store, channel, result)
    assert "failed candidate writes" in result.output
    # Neither an explicit check nor an unrelated successful write clears the
    # final error the model has not yet repaired.
    _check_all(registry, context)
    other = tmp_path / "other.py"
    other.write_text("x = 1\n")
    assert registry.execute("edit_file", {"path": "other.py", "old": "x = 1", "new": "x = 2"}, context).ok
    _assert_unwritten(store, channel, _submit(registry, context))
    assert registry.execute("edit_file", {"path": "./app.py", "old": "value = 2", "new": "value = 3"}, context).ok
    assert target.read_text() == "value = 3\n"
    _assert_unwritten(store, channel, _submit(registry, context))
    # Even a later same-path edit may represent a different intention. Only a
    # newly admitted owner response can acknowledge all preceding tool errors.
    cap.begin_model_response(context=context)
    assert _submit(registry, context).ok


def test_expired_check_retry_revokes_its_earlier_pass(tmp_path):
    cap, registry, context, _, store, channel, *_ = _gated(tmp_path)
    _check_all(registry, context)
    cap.reserve_handoff_time(time.monotonic() - 1)
    assert not registry.execute("executor_check", {"check": 2}, context).ok
    _assert_unwritten(store, channel, _submit(registry, context))


def test_uncertain_write_exception_invalidates_prior_passes(tmp_path):
    def uncertain(_value, _context):
        raise RuntimeError("writer failed after an uncertain operation")

    cap, registry, context, _, store, channel, *_ = _gated(tmp_path, writer=replace(edit_file_tool, run=uncertain))
    _check_all(registry, context)
    # Even unchanged bytes cannot make old receipts certify an uncertain write.
    result = registry.execute("edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 2"}, context)
    assert not result.ok
    _assert_unwritten(store, channel, _submit(registry, context))
    assert cap.supports_conditional_submit


@pytest.mark.parametrize("status", ["blocked", "failed"])
def test_honest_unfinished_handoff_does_not_require_passing_checks(tmp_path, status):
    _, registry, context, _, store, channel, *_ = _gated(tmp_path)
    value = _submission()
    value.update(status=status, blockers=["An approved check still fails."])
    value["steps"][1]["status"] = "failed"
    result = registry.execute("executor_submit", value, context)
    assert result.ok and result._implementation_submitted, result.output
    assert json.loads(store.read(channel.write_key).content)["status"] == status


def test_closed_gate_revokes_completed_and_current_digest_callback(tmp_path):
    cap, registry, context, state, store, channel, *_ = _gated(tmp_path)
    _check_all(registry, context)
    cap.close()
    before = state["digest_calls"]
    assert not cap.supports_conditional_submit
    _assert_unwritten(store, channel, _submit(registry, context))
    assert state["digest_calls"] == before


def test_owner_mismatch_precedes_candidate_gate(tmp_path):
    _, registry, _, state, store, channel, *_ = _gated(tmp_path)
    result = _submit(registry, ToolContext(cwd=str(tmp_path)))
    _assert_unwritten(store, channel, result)
    assert "executor_submit_owner" in result.output
    assert state["digest_calls"] == 0


def test_note_write_cannot_bypass_installed_native_gate(tmp_path):
    _, registry, context, _, store, channel, _, assignment, order = _gated(tmp_path)
    _check_all(registry, context)
    declaration = {"resultVersion": 1, "assignmentId": assignment.assignment_id, "orderId": order.order_id,
                   "status": "completed", "stepResults": [
                       {"stepId": step.step_id, "status": "completed", "artifactRefs": []} for step in order.steps],
                   "deviations": [], "selfChecks": [], "blockers": []}
    result = registry.execute("subagent_note_write", {"key": channel.write_key, "content": json.dumps(declaration)}, context)
    _assert_unwritten(store, channel, result)
    assert "executor_submit" in result.output
    assert _submit(registry, context).ok


def test_only_real_native_binding_and_digest_callback_enable_conditional_support(tmp_path):
    cap, registry, context, _, _, _, *_ = _gated(tmp_path, with_digest=False)
    assert not cap.supports_conditional_submit
    assert _submit(registry, context).ok, "historical capabilities without current digest keep old semantics"
    _, _, _, _, tool = _bound()
    fake = replace(tool, run=lambda *_: ToolResult(ok=True, output="unbound"))
    cap = ExecutorCheckCapability((VerificationCheck("a", "tests", ("pytest",), 1),),
                                  lambda *_: {}, current_patch_digest=lambda _: "digest")
    cap.bind_registry(ToolRegistry([fake]))
    assert not cap.supports_conditional_submit


def test_reserved_handoff_deadline_blocks_underlying_write(tmp_path):
    cap, registry, context, state, store, channel, target, *_ = _gated(tmp_path)
    _check_all(registry, context)
    cap.reserve_handoff_time(time.monotonic() - 1)
    result = registry.execute("edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 2"}, context)
    assert not result.ok and "handoff_reserved" in result.output
    assert target.read_text() == "value = 1\n"
    assert state["runs"] == ["a", "b"]
    _assert_unwritten(store, channel, _submit(registry, context))


def test_approval_crossing_fixed_handoff_blocks_actual_atomic_write(tmp_path, monkeypatch):
    cap, registry, context, _, store, channel, target, *_ = _gated(tmp_path)
    _check_all(registry, context)
    started = time.monotonic()
    clock = {"now": started}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])

    class Permissions:
        approvals = 0

        def ensure_path_access(self, _path, _intent):
            pass

        def ensure_edit(self, _path, _diff):
            self.approvals += 1
            clock["now"] = started + 3

        def ensure_operation_active(self):
            pass

    permissions = Permissions()
    context = replace(context, permissions=permissions, _task_deadline_monotonic=started + 10)
    cap.reserve_handoff_time(started + 2)
    result = registry.execute("edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 2"}, context)
    assert permissions.approvals == 1, "real writer entered review before the reserved boundary"
    assert not result.ok and "TaskDeadlineExceeded" in result.output
    assert target.read_text() == "value = 1\n", "post-approval checkpoint must precede the atomic write"
    assert context._task_deadline_monotonic == started + 10, "handoff keeps its original owner deadline"
    _assert_unwritten(store, channel, _submit(registry, context))
    value = _submission()
    value.update(status="blocked", blockers=["The approved edit outlasted its reserved implementation time."])
    value["steps"][1]["status"] = "failed"
    result = registry.execute("executor_submit", value, context)
    assert result.ok and result._implementation_submitted, result.output


@pytest.mark.parametrize("tool_name,tool_input", [
    ("edit_file", {"path": "app.py", "old": "value = 1"}),
    ("write_file", {"path": "app.py"}),
    ("executor_test", {"path": "test_app.py"}),
    ("executor_check", {"check": 1}),
    ("executor_check", {"check": "invalid"}),
])
def test_host_pipeline_rejection_invalidates_old_pass_without_running_tool(tmp_path, tool_name, tool_input):
    cap, registry, context, state, store, channel, target, *_ = _gated(tmp_path)
    _check_all(registry, context)
    # Validator, permission, phase and recovery denials can all occur before
    # the wrapped run. The host reports their actual result at that boundary.
    if tool_name == "edit_file":
        result = registry.execute(tool_name, tool_input, context)
        assert not result.ok, "real registry validator must reject the incomplete edit"
    else:
        result = ToolResult(ok=False, output="host pipeline denied this invocation")
    cap.observe_tool_result(tool_name, tool_input, result, context=context)
    epoch = cap._mutation_epoch
    cap.observe_tool_result(tool_name, tool_input, result, context=context)
    assert cap._mutation_epoch == epoch, "observing a wrapped failure twice is idempotent"
    _assert_unwritten(store, channel, _submit(registry, context))
    assert target.read_text() == "value = 1\n"
    assert state["runs"] == ["a", "b"]


def test_next_admitted_owner_response_can_correct_rejected_path_without_old_receipts(tmp_path):
    cap, registry, context, _, store, channel, target, *_ = _gated(tmp_path)
    _check_all(registry, context)
    # No actual access to this path occurs: the host rejected it before run.
    cap.observe_tool_result("write_file", {"path": "/denied/destination.py", "content": "x"},
                            ToolResult(ok=False, output="permission denied"), context=context)
    cap.begin_model_response(context=ToolContext(cwd=str(tmp_path)))
    assert cap._unresolved_write_errors, "another owner cannot acknowledge this response"
    _assert_unwritten(store, channel, _submit(registry, context))
    cap.begin_model_response(context=context)
    assert not cap._unresolved_write_errors
    _assert_unwritten(store, channel, _submit(registry, context))
    # Only fresh real checks after the owner chooses a valid path can hand off.
    result = registry.execute("edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 2"}, context)
    assert result.ok and target.read_text() == "value = 2\n"
    assert _submit(registry, context).ok


def test_check_started_before_a_real_write_cannot_restore_old_receipts(tmp_path):
    cap, registry, context, _, store, channel, *_ = _gated(tmp_path)
    _check_all(registry, context)
    started = threading.Event()
    release = threading.Event()
    run_check = cap._run_check

    def delayed(check, worker_context):
        receipt = run_check(check, worker_context)
        started.set()
        assert release.wait(2)
        return receipt

    cap._run_check = delayed
    results = []
    thread = threading.Thread(target=lambda: results.append(
        registry.execute("executor_check", {"check": 1}, context)))
    thread.start()
    try:
        assert started.wait(2)
        batch = cap.begin_write_batch()
        assert registry.execute("edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 2"}, context).ok
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and results[0].ok
    _assert_unwritten(store, channel, _submit(registry, context))
    cap._run_check = run_check
    document, error = batch.finish()
    assert error is None and document["status"] == "passed"
    assert _submit(registry, context).ok
