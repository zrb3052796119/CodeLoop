"""Repair projection preserves evidence while accounting for real savings."""

import copy
import json

import pytest

from minicode.implementation_recovery import (
    InputTokenCalibration, project_recovery_messages,
)


def _pair(identity, name, path, body, *, failed=False, start=1):
    return [
        {"role": "assistant_tool_call", "toolUseId": identity,
         "toolName": name, "assistantTurnId": "turn-" + identity,
         "input": {"path": path, "start_line": start},
         "reasoningContent": "original provider reasoning " + identity},
        {"role": "tool_result", "toolUseId": identity, "toolName": name,
         "content": body, "isError": failed},
    ]


def _history():
    messages = [
        {"role": "system", "content": "Current permissions and Assignment"},
        {"role": "user", "content": "Original task and acceptance criteria"},
        {"role": "user", "content": "old source packet " * 2000,
         "_implementation_context": True},
        {"role": "user", "content": "Correction: retain substantive findings"},
    ]
    messages += _pair("old-source", "read_file", "task.py", "old window " * 1000)
    messages += _pair("old-search", "grep_files", "task.py", "old matches " * 1000)
    messages += _pair("latest-source", "read_file", "task.py", "latest window " * 1000,
                      start=2800)
    messages += _pair("only-other-source", "read_file", "other.py", "other source " * 1000)
    # Six non-read results mean all the old reads are outside the recent tail.
    for index in range(3):
        messages += _pair(f"check-{index}", "executor_check", "task.py",
                          f'{{"checkId":"check{index}","status":"failed",'
                          f'"patchDigest":"candidate-{index}"}}', failed=True)
    messages += _pair("passing-check", "executor_check", "task.py",
                      '{"checkId":"check0","status":"passed","patchDigest":"current"}')
    messages += _pair("skill", "load_skill", "SKILL.md", "required instructions " * 1000)
    messages += _pair("failed-edit", "edit_file", "task.py",
                      "Found 2 matches for the search string.\nMatches at lines: 2735, 2879",
                      failed=True)
    messages[-2]["input"] = {"path": "task.py", "old": "duplicated text", "new": "fixed text"}
    return messages


def test_projection_leaves_canonical_and_tool_protocol_unchanged():
    messages = _history()
    original = copy.deepcopy(messages)
    projected = project_recovery_messages(messages)

    assert messages == original
    assert len(projected) == len(messages)
    assert all(before is not after for before, after in zip(messages, projected))
    for before, after in zip(messages, projected):
        assert {k: v for k, v in before.items() if k != "content"} == {
            k: v for k, v in after.items() if k != "content"}
        if before["role"] != "tool_result" and not before.get("_implementation_context"):
            assert before == after
    assert "omitted from this repair request" in projected[2]["content"]
    assert project_recovery_messages(projected) == projected


def test_only_old_windows_retire_and_latest_path_evidence_survives():
    messages = _history()
    projected = project_recovery_messages(messages)
    old = {m["toolUseId"]: m for m in messages if m["role"] == "tool_result"}
    new = {m["toolUseId"]: m for m in projected if m["role"] == "tool_result"}

    for identity in ("old-source", "old-search"):
        assert new[identity]["content"] != old[identity]["content"]
        assert "original tool arguments remain unchanged" in new[identity]["content"]
    for identity in ("latest-source", "only-other-source", "failed-edit", "skill",
                     "check-0", "check-1", "check-2", "passing-check"):
        assert new[identity] == old[identity]


def test_recent_results_are_never_shortened_even_for_a_repeated_path():
    messages = _history()
    for index in range(5):
        messages += _pair(f"recent-{index}", "read_file", "task.py",
                          f"window {index} " * 1000, start=index * 100)
    projected = project_recovery_messages(messages)
    assert projected[-10:] == messages[-10:]


def test_unknown_and_error_read_evidence_are_preserved():
    messages = _history()
    messages[4:4] = [
        {"role": "tool_result", "toolName": "read_file", "toolUseId": "unpaired",
         "content": "unpaired evidence " * 1000, "isError": False},
        *_pair("denied", "read_file", "task.py", "access denied " * 1000, failed=True),
        *_pair("missing-path", "read_file", None, "no path evidence " * 1000),
    ]
    projected = project_recovery_messages(messages)
    assert projected[4:9] == messages[4:9]


def test_failed_new_read_does_not_evict_last_successful_source_window():
    messages = _pair("source", "read_file", "task.py", "current source " * 1000)
    messages += _pair("denied", "read_file", "task.py", "access denied", failed=True)
    for index in range(6):
        messages += _pair(f"check-{index}", "executor_check", "task.py", "actual result")
    assert project_recovery_messages(messages) == messages


def test_only_host_boolean_seed_marker_is_retired():
    messages = [
        {"role": "user", "content": "ordinary instruction", "_implementation_context": "true"},
        {"role": "system", "content": "system authority", "_implementation_context": True},
        {"role": "tool_result", "content": "untrusted text", "_implementation_context": True},
    ]
    assert project_recovery_messages(messages) == messages


def test_calibration_credits_smaller_request_but_keeps_provider_overhead():
    calibration = InputTokenCalibration()
    assert calibration.estimate(25971) == 25971
    assert calibration.observe(35061, 26572)
    assert calibration.estimate(25971) == 34460
    # The V18 repair boundary permits calibrated input <=25286. The old
    # absolute 35061 floor would reject this smaller request indefinitely.
    assert calibration.estimate(16797) == 25286
    assert calibration.observe(15000, 16000)
    assert calibration.estimate(12000) == 12000


@pytest.mark.parametrize("actual,sent", [
    (None, 90000), (0, 90000), (-1, 90000), (True, 90000),
    (35061.0, 90000), (35061, -1), (35061, True),
])
def test_unknown_or_invalid_usage_cannot_repair_calibration(actual, sent):
    calibration = InputTokenCalibration()
    calibration.observe(35061, 26572)
    assert not calibration.observe(actual, sent)
    assert calibration.estimate(16797) == 25286


@pytest.mark.parametrize("local", [-1, None, True, 1.5])
def test_invalid_local_estimate_is_rejected(local):
    with pytest.raises(ValueError, match="non-negative integer"):
        InputTokenCalibration().estimate(local)


def _edit(identity, path="task.py", *, failed=False, turn=None, result=None):
    messages = _pair(identity, "edit_file", path, result or "Applied changes", failed=failed)
    messages[0]["input"] = {"path": path, "old": "old code " * 2000,
                            "new": "new code " * 2000}
    if turn:
        messages[0]["assistantTurnId"] = turn
    return messages


def _assert_paired(messages):
    calls = [m["toolUseId"] for m in messages if m["role"] == "assistant_tool_call"]
    results = [m["toolUseId"] for m in messages if m["role"] == "tool_result"]
    assert sorted(calls) == sorted(results)
    assert len(calls) == len(set(calls))


def _complete_read(tmp_path, identity, path="task.py"):
    from minicode.tooling import ToolContext
    from minicode.tools.read_file import read_file_tool

    (tmp_path / path).write_text("new code " * 20)
    values = {"path": path}
    result = read_file_tool.run(read_file_tool.validator(values), ToolContext(cwd=str(tmp_path)))
    assert result.ok
    messages = _pair(identity, "read_file", path, result.output)
    messages[0]["input"] = values
    return messages


def test_superseded_successful_edit_turn_retires_arguments_but_keeps_exact_results(tmp_path):
    failed_check = ('Applied reviewed changes\n=== IMPLEMENTATION CHECK FEEDBACK ===\n'
                    '[{"checkId":"check1","status":"failed","patchDigest":"old",'
                    '"stdout":"AssertionError: preserve findings"}]')
    messages = _edit("old", result=failed_check)
    messages[0]["input"]["old"] = "retired old source " * 2000
    messages += _complete_read(tmp_path, "current-source")
    messages += _edit("new", result='Applied changes\n{"status":"passed","patchDigest":"new"}')
    messages += _edit("failure", failed=True, result="Found 2 matches")
    original = copy.deepcopy(messages)
    projected = project_recovery_messages(messages)

    assert messages == original
    assert len(projected) == 7
    assert projected[0]["role"] == "assistant"
    assert projected[0]["_implementation_recovery_summary"] is True
    assert failed_check in projected[0]["content"]
    assert '"toolUseId": "old"' in projected[0]["content"]
    assert '"path": "task.py"' in projected[0]["content"]
    assert "not final verification" in projected[0]["content"]
    assert "retired old source " * 2000 not in str(projected)
    assert projected[1:] == messages[2:]
    _assert_paired(projected)
    assert project_recovery_messages(projected) == projected


def test_edit_retirement_is_atomic_for_a_whole_provider_turn(tmp_path):
    messages = _edit("old-a", path="a.py", turn="batch")
    messages += _edit("old-b", path="b.py", turn="batch")
    messages += _complete_read(tmp_path, "current-a", path="a.py")
    # A complete read of only a.py cannot replace b.py's current edit.
    assert project_recovery_messages(messages) == messages

    messages += _complete_read(tmp_path, "current-b", path="b.py")
    projected = project_recovery_messages(messages)
    assert len(projected) == 5
    assert '"toolUseId": "old-a"' in projected[0]["content"]
    assert '"toolUseId": "old-b"' in projected[0]["content"]
    assert projected[1:] == messages[4:]
    _assert_paired(projected)


@pytest.mark.parametrize("case", ["mixed", "failed", "unpaired", "authority", "interruption"])
def test_edit_retirement_preserves_mixed_incomplete_failed_or_authority_turns(tmp_path, case):
    messages = _edit("old", turn="batch")
    if case == "mixed":
        extra = _pair("check", "executor_check", "task.py", '{"status":"failed"}')
        extra[0]["assistantTurnId"] = "batch"
        messages += extra
    elif case == "failed":
        messages[1]["isError"] = True
    elif case == "unpaired":
        messages.pop()
    elif case == "authority":
        messages[1]["_must_deliver"] = True
    else:
        messages.insert(1, {"role": "user", "content": "New instruction inside interrupted turn"})
    messages += _complete_read(tmp_path, "current-source")
    assert project_recovery_messages(messages) == messages


def _phase_fixture(*, used_tokens=419736, used_calls=18):
    from minicode.agent_budget import AgentTurnBudget
    from minicode.implementation_phases import ImplementationPhaseController
    from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
    from minicode.tools.read_file import read_file_tool
    from minicode.tools.edit_file import edit_file_tool
    from minicode.workflow_runner import _WorkerCallBudget

    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    for index in range(used_calls):
        reservation = shared.reserve_model_call(0)
        shared.record_model_call(input_tokens=used_tokens if index == 0 else 0,
                                 reservation=reservation)
    worker = _WorkerCallBudget(shared, "executor", max_calls=36,
                               preserve_calls=1, preserve_tokens=19452)
    policy = ImplementationPhaseController(snapshot=worker.snapshot(), max_steps=36,
        now=0, deadline=600, analysis_complete=True, allow_test_completion=True,
        require_structured_handoff=True)
    submit = ToolDefinition(name="executor_submit", description="existing result channel",
        input_schema={"type": "object"}, validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output="received"))
    return shared, worker, policy, ToolRegistry([read_file_tool, edit_file_tool, submit])


def test_phase_preview_never_mutates_closing_state_or_consumes_allowance():
    shared, worker, policy, _registry = _phase_fixture()
    policy.observe_edit(path="task.py", anchor_failed=True, changed=False)
    before = copy.deepcopy(vars(policy))
    snapshot = shared.snapshot()
    for estimate in (35061, 16797, 35061):
        policy.preview(worker.snapshot(), estimated_input=estimate, now=480)
        assert vars(policy) == before
        assert shared.snapshot() == snapshot
    assert policy.preview(worker.snapshot(), estimated_input=35061, now=480).handoff_only
    assert not policy.preview(worker.snapshot(), estimated_input=25200, now=480).handoff_only


def test_anchor_repair_and_submit_are_admitted_inside_original_worker_floor():
    shared, worker, policy, registry = _phase_fixture()
    policy.observe_edit(path="task.py", anchor_failed=True, changed=False)
    assert worker.snapshot().limit_total_tokens == 480548
    request = policy.request(worker.snapshot(), estimated_input=25200, now=480)
    assert not request.handoff_only
    assert request.preserve_calls == 1
    names = {tool.name for tool in policy.tool_view(registry).list()}
    assert {"read_file", "edit_file", "executor_submit"} <= names
    assert not policy.allowed_name("grep_files")
    focused = {"id": "read", "toolName": "read_file",
               "input": {"path": "task.py", "offset": 2800, "limit": 8000}}
    assert policy.admit_batch([focused]) == {}
    assert "read" in policy.admit_batch([{**focused, "input": {"path": "task.py", "limit": 8001}}])
    reservation = worker.reserve_model_call(25200,
        preserve_model_calls=request.preserve_calls, preserve_total_tokens=request.preserve_tokens)
    policy.admitted()
    worker.record_model_call(input_tokens=25200, output_tokens=4096, reservation=reservation)
    policy.observe_edit(path="task.py", anchor_failed=False, changed=True)
    final = policy.request(worker.snapshot(), estimated_input=27248, now=520)
    assert final.handoff_only
    assert {tool.name for tool in policy.tool_view(registry).list()} == {"executor_submit"}
    reservation = worker.reserve_model_call(27248,
        preserve_model_calls=final.preserve_calls, preserve_total_tokens=final.preserve_tokens)
    policy.admitted()
    worker.record_model_call(input_tokens=27248, output_tokens=2048, reservation=reservation)
    assert policy.exhausted
    assert shared.snapshot().used_total_tokens == 478328
    assert shared.snapshot().used_total_tokens <= 500000 - 19452
    assert shared.snapshot().used_model_calls == 20
    # The parent's floor remains an actual usable reservation in the same ledger.
    closing = shared.reserve_model_call(17000, preserve_total_tokens=2048)
    shared.record_model_call(input_tokens=17000, output_tokens=2048, reservation=closing)
    assert shared.snapshot().used_total_tokens <= 500000


@pytest.mark.parametrize("boundary", ["tokens", "calls", "time", "admitted"])
def test_anchor_failure_never_reopens_a_real_handoff_boundary(boundary):
    used_calls = 38 if boundary == "calls" else 18
    _shared, worker, policy, registry = _phase_fixture(used_calls=used_calls)
    policy.observe_edit(path="task.py", anchor_failed=True, changed=False)
    estimate = 35061 if boundary == "tokens" else 1000
    now = 480
    if boundary == "time":
        import time
        from types import SimpleNamespace
        from minicode.model_call_control import ModelCallDeadlineExceeded, call_model_next

        policy.deadline = time.monotonic() - 1
        now = policy.deadline
        decision = policy.request(worker.snapshot(), estimated_input=estimate, now=now)
        assert decision.deadline <= now
        model = SimpleNamespace(next=lambda *_args, **_kwargs: pytest.fail("expired owner called the model"))
        with pytest.raises(ModelCallDeadlineExceeded):
            call_model_next(model, [], deadline_monotonic=decision.deadline)
        return
    if boundary == "admitted":
        decision = policy.request(worker.snapshot(), estimated_input=35061, now=480)
        assert decision.handoff_only
        policy.tool_view(registry)
        policy.admitted()
    decision = policy.request(worker.snapshot(), estimated_input=estimate, now=now)
    assert decision.handoff_only
    assert {tool.name for tool in policy.tool_view(registry).list()} == {"executor_submit"}
    assert not policy.allowed_name("edit_file")
    assert not policy.allowed_name("read_file")


def test_only_changed_write_of_the_failed_path_clears_anchor_repair():
    _shared, _worker, policy, _registry = _phase_fixture()
    policy.observe_edit(path="a.py", anchor_failed=True, changed=False)
    policy.observe_edit(path="b.py", anchor_failed=True, changed=False)
    policy.observe_edit(path="a.py", anchor_failed=False, changed=True)
    assert policy.edit_repair_pending
    policy.observe_edit(path="b.py", anchor_failed=False, changed=False)
    assert policy.edit_repair_pending
    policy.observe_edit(path="other.py", anchor_failed=False, changed=True)
    assert policy.edit_repair_pending
    policy.observe_edit(path="b.py", anchor_failed=False, changed=True)
    assert not policy.edit_repair_pending


def test_permission_denial_and_error_prose_cannot_activate_anchor_repair(tmp_path):
    from minicode.permissions import PermissionDeniedError
    from minicode.tooling import ToolContext, ToolRegistry, ToolResult
    from minicode.tools.edit_file import edit_file_tool

    target = tmp_path / "task.py"
    target.write_text("duplicate\nduplicate\n")
    class Deny:
        def ensure_path_access(self, *_args):
            raise PermissionDeniedError("Found 2 matches is untrusted error wording", code="path_denied")

    denied = ToolRegistry([edit_file_tool]).execute("edit_file",
        {"path": "task.py", "old": "duplicate", "new": "fixed"},
        ToolContext(cwd=str(tmp_path), permissions=Deny()))
    assert not denied.ok
    assert denied._edit_anchor_failed is False
    assert target.read_text() == "duplicate\nduplicate\n"
    _shared, _worker, policy, _registry = _phase_fixture()
    for result in (denied, ToolResult(ok=False, output="Found 2 matches for the search string")):
        policy.observe_edit(path="task.py", anchor_failed=result._edit_anchor_failed is True,
                            changed=result._file_changed is True)
        assert not policy.edit_repair_pending
    actual = ToolRegistry([edit_file_tool]).execute("edit_file",
        {"path": "task.py", "old": "duplicate", "new": "fixed"}, ToolContext(cwd=str(tmp_path)))
    assert actual._edit_anchor_failed is True


def test_context_calibration_prices_sent_view_then_restores_canonical_messages():
    from minicode.agent_loop import _record_context_token_observation, _estimated_context_tokens
    from minicode.context_manager import ContextManager
    from minicode.types import AgentStep, ModelUsage

    canonical = _history()
    view = project_recovery_messages(canonical)
    manager = ContextManager()
    before = copy.deepcopy(canonical)
    reported = 2 * _estimated_context_tokens(view)
    step = AgentStep(type="assistant", usage=ModelUsage(input_tokens=reported, source="provider"))
    _record_context_token_observation(manager, canonical, step, request_messages=view)
    assert manager._token_calibration == pytest.approx(1.2)
    assert manager.messages is canonical
    assert canonical == before


def test_read_dedup_cannot_point_to_retired_model_view_text():
    from minicode.context_compactor import ReadDedupManager

    canonical = _history()
    old_index = next(i for i, message in enumerate(canonical)
                     if message.get("role") == "tool_result" and message.get("toolUseId") == "old-source")
    content = canonical[old_index]["content"]
    dedup = ReadDedupManager()
    dedup.register_read("task.py", content, old_index)
    assert dedup.should_dedup("task.py", content)
    dedup.reconcile(project_recovery_messages(canonical))
    assert not dedup.should_dedup("task.py", content)
    assert canonical[old_index]["content"] == content


def test_agent_loop_pairs_calibration_only_with_real_provider_input(tmp_path, monkeypatch):
    import minicode.implementation_recovery as recovery
    from minicode.agent_loop import run_agent_turn, _estimated_context_tokens
    from minicode.tooling import ToolRegistry
    from minicode.tools.edit_file import edit_file_tool
    from minicode.tools.read_file import read_file_tool
    from minicode.types import AgentStep, ModelUsage

    observations, requests = [], []
    class ObservedCalibration(InputTokenCalibration):
        def observe(self, actual_input, sent_local):
            observations.append((actual_input, sent_local))
            return super().observe(actual_input, sent_local)
    monkeypatch.setattr(recovery, "InputTokenCalibration", ObservedCalibration)
    (tmp_path / "task.py").write_text("value = 1\n")
    usages = [ModelUsage(input_tokens=35061, output_tokens=1, source="provider"),
              ModelUsage(input_tokens=90000, output_tokens=1, source="estimated"),
              ModelUsage(source="unavailable"),
              ModelUsage(input_tokens=20000, output_tokens=1, source="provider")]
    class Model:
        def next(self, messages, **_kwargs):
            index = len(requests)
            requests.append(_estimated_context_tokens(messages))
            if index == 3:
                return AgentStep(type="assistant", content="Observed the file.", usage=usages[index])
            return AgentStep(type="tool_calls", calls=[{"id": f"read-{index}",
                "toolName": "read_file", "input": {"path": "task.py"}}], usage=usages[index])
    run_agent_turn(model=Model(), tools=ToolRegistry([read_file_tool, edit_file_tool]),
        messages=[{"role": "user", "content": "Fix the authorized implementation."}],
        cwd=str(tmp_path), implementation_task=True, implementation_analysis_complete=True,
        enable_work_chain=False, agent_depth=1, max_steps=10)
    assert len(requests) == 4
    assert observations == [(35061, requests[0]), (20000, requests[3])]


def _feedback(identity, *, turn=None, command="sha256:command", patch=None,
              status="failed", tool="executor_check", host_marker=True):
    receipt = {
        "checkId": "check1", "status": status, "patchDigest": patch or "sha256:" + identity,
        "argv": ["python3", "-m", "pytest", "-q", "tests/test_regression.py"],
        "verification": "implementation_feedback", "writeback": "pending",
        "evidence": {
            "checkId": "check1", "commandDigest": command, "kind": "tests",
            "status": status, "exitCode": 1 if status == "failed" else 0,
            "stdout": {"preview": "assertion and test evidence " * 1000,
                       "previewBase64": "YWN0dWFs" * 1000,
                       "byteCount": 28000, "digest": "sha256:stdout", "truncated": False},
            "stderr": {"preview": "stderr observation " * 500,
                       "byteCount": 9500, "digest": "sha256:stderr", "truncated": False},
        },
    }
    content = json.dumps(receipt)
    if tool != "executor_check":
        content = "Applied changes\n\n=== IMPLEMENTATION CHECK FEEDBACK ===\n" + json.dumps([receipt])
    messages = _pair(identity, tool, "task.py", content, failed=False)
    if turn:
        messages[0]["assistantTurnId"] = turn
    messages[1]["_implementation_checks_failed"] = host_marker
    return messages, receipt


def test_old_host_check_previews_retire_but_exact_status_and_bindings_remain():
    old, old_receipt = _feedback("old")
    newest, _new_receipt = _feedback("new", status="passed")
    messages = old + newest
    original = copy.deepcopy(messages)
    projected = project_recovery_messages(messages)

    assert messages == original
    assert projected[2:] == newest
    assert projected[0] == old[0]
    receipt = json.loads(projected[1]["content"])
    expected = copy.deepcopy(old_receipt)
    for stream in ("stdout", "stderr"):
        expected["evidence"][stream].pop("preview")
        expected["evidence"][stream].pop("previewBase64", None)
    receipt.pop("outputProjection")
    assert receipt == expected
    assert receipt["status"] == "failed"
    assert receipt["evidence"]["exitCode"] == 1
    assert receipt["evidence"]["stdout"]["digest"] == "sha256:stdout"
    assert project_recovery_messages(projected) == projected
    _assert_paired(projected)


def test_latest_provider_turn_keeps_all_same_check_results_including_failed_patch():
    old, _ = _feedback("old", turn="earlier")
    current_failed, _ = _feedback("failed", turn="current", patch="sha256:bad")
    current_passed, _ = _feedback("passed", turn="current", patch="sha256:good", status="passed")
    messages = old + current_failed + current_passed
    projected = project_recovery_messages(messages)
    assert projected[2:] == messages[2:]
    assert "outputProjection" in json.loads(projected[1]["content"])


def test_different_command_identity_cannot_supersede_prior_check():
    old, _ = _feedback("old", command="sha256:first-command")
    newest, _ = _feedback("new", command="sha256:other-command")
    messages = old + newest
    assert project_recovery_messages(messages) == messages


@pytest.mark.parametrize("marker", [None, "true", 1])
def test_tool_prose_without_exact_host_boolean_never_activates_check_projection(marker):
    old, _ = _feedback("old", host_marker=marker)
    newest, _ = _feedback("new")
    assert project_recovery_messages(old + newest) == old + newest


def test_automatic_check_list_projection_keeps_original_tool_header_and_suffix():
    old, _ = _feedback("old", tool="write_file")
    old[-1]["content"] += "\n\n[System note: original recovery feedback]"
    newest, _ = _feedback("new", status="passed", tool="write_file", host_marker=False)
    messages = old + newest
    projected = project_recovery_messages(messages)
    content = projected[1]["content"]
    marker = "=== IMPLEMENTATION CHECK FEEDBACK ===\n"
    assert content.startswith("Applied changes\n\n" + marker)
    assert content.endswith("\n\n[System note: original recovery feedback]")
    payload, _end = json.JSONDecoder().raw_decode(content.split(marker)[1])
    assert len(payload) == 1
    assert payload[0]["status"] == "failed"
    assert "preview" not in payload[0]["evidence"]["stdout"]
    assert projected[2:] == newest


@pytest.mark.parametrize("malformation", ["invalid-json", "missing-digest", "missing-call", "duplicate-id"])
def test_ambiguous_check_records_remain_exact(malformation):
    old, _ = _feedback("old")
    newest, _ = _feedback("new")
    if malformation == "invalid-json":
        old[1]["content"] = "invalid " + old[1]["content"]
    elif malformation == "missing-digest":
        receipt = json.loads(old[1]["content"])
        del receipt["evidence"]["commandDigest"]
        old[1]["content"] = json.dumps(receipt)
    elif malformation == "missing-call":
        old.pop(0)
    else:
        old.insert(0, copy.deepcopy(old[0]))
    messages = old + newest
    assert project_recovery_messages(messages) == messages


def test_historical_edit_summary_preserves_failed_check_binding_without_old_stream_body(tmp_path):
    old, receipt = _feedback("old", tool="edit_file")
    receipt["evidence"]["stdout"]["preview"] = "old unique assertion evidence " * 1000
    old[1]["content"] = "Applied changes\n=== IMPLEMENTATION CHECK FEEDBACK ===\n" + json.dumps([receipt])
    old[0]["input"] = {"path": "task.py", "old": "old implementation " * 2000,
                        "new": "intermediate implementation " * 2000}
    newest, _ = _feedback("new", tool="edit_file", status="passed", host_marker=False)
    current = _complete_read(tmp_path, "current-source")
    canonical = old + current + newest
    original = copy.deepcopy(canonical)
    projected = project_recovery_messages(canonical)
    assert canonical == original
    assert len(projected) == 5
    assert projected[0]["_implementation_recovery_summary"] is True
    assert '"status": "failed"' in projected[0]["content"]
    assert '"commandDigest": "sha256:command"' in projected[0]["content"]
    assert '"patchDigest": "sha256:old"' in projected[0]["content"]
    assert "outputProjection" in projected[0]["content"]
    assert "old unique assertion evidence" not in str(projected)
    assert projected[1:] == current + newest
    _assert_paired(projected)


def _sequenced_feedback(identity, sequence, *, turn="current", **kwargs):
    messages, receipt = _feedback(identity, turn=turn, **kwargs)
    if sequence is not None:
        receipt["observationSequence"] = sequence
    messages[1]["content"] = json.dumps(receipt)
    return messages


def test_host_observation_sequence_orders_same_turn_without_using_call_order():
    # The first call's check finished after the second call's check. Their
    # tool results are delivered in original call order, as in a real batch.
    latest_observation = _sequenced_feedback("newer-output", 7, status="passed", patch="sha256:candidate-a")
    older_observation = _sequenced_feedback("older-output", 6, status="failed", patch="sha256:candidate-b")
    messages = latest_observation + older_observation
    original = copy.deepcopy(messages)
    projected = project_recovery_messages(messages)
    assert projected[:2] == latest_observation
    older = json.loads(projected[3]["content"])
    assert "preview" not in older["evidence"]["stdout"]
    assert older["status"] == "failed"
    assert older["patchDigest"] == "sha256:candidate-b"
    assert older["observationSequence"] == 6
    assert older["verification"] == "implementation_feedback"
    assert older["writeback"] == "pending"
    assert messages == original


@pytest.mark.parametrize("other", [None, 0, -1, True, 7.0, "7"])
def test_missing_or_invalid_sequence_keeps_entire_latest_check_group(other):
    first = _sequenced_feedback("first", 8)
    second = _sequenced_feedback("second", other)
    messages = first + second
    assert project_recovery_messages(messages) == messages


def test_tied_observation_sequences_keep_every_latest_result():
    first = _sequenced_feedback("first", 8, patch="sha256:one")
    second = _sequenced_feedback("second", 8, patch="sha256:two")
    messages = first + second
    assert project_recovery_messages(messages) == messages


def test_observation_sequence_is_local_to_turn_not_compared_across_capability_resets():
    previous_capability = _sequenced_feedback("old-cap", 100, turn="previous")
    current_older = _sequenced_feedback("current-older", 1)
    current_latest = _sequenced_feedback("current-latest", 2)
    messages = previous_capability + current_latest + current_older
    projected = project_recovery_messages(messages)
    assert projected[2:4] == current_latest
    for index in (1, 5):
        receipt = json.loads(projected[index]["content"])
        assert "preview" not in receipt["evidence"]["stdout"]
        assert receipt["status"] == "failed"


def test_legacy_prior_turn_does_not_invalidate_current_turn_host_order():
    legacy = _sequenced_feedback("legacy", None, turn="legacy")
    current_first = _sequenced_feedback("current-first", 1)
    current_last = _sequenced_feedback("current-last", 2)
    projected = project_recovery_messages(legacy + current_first + current_last)
    assert projected[-2:] == current_last
    assert "preview" not in json.loads(projected[3]["content"])["evidence"]["stdout"]
