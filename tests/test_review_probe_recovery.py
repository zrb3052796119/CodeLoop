"""The real registry retrieves immutable probe data without broad Store access.

The optional reader registration keeps this same test executable on frozen
production: the old failure is Unknown tool after a real probe publication.
"""

from dataclasses import replace
import hashlib
import json
import threading
import time

import pytest

from minicode.review_probe_source import review_probe_source_artifact_id
from minicode.reviewer_verification import ReviewerCheckCapability, ReviewerProbeLedger
from minicode.task_deadline import TaskDeadlineExceeded
from minicode.tooling import ToolContext, ToolExecutionAbandoned, ToolRegistry
from minicode.tools.subagent_notes import subagent_note_read_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from test_reviewer_result_submission import _bound, _rejection
from test_reviewer_verification import PATCH, _receipt
from test_review_probe_correction import correction_case, _input, _correction  # noqa: F401
from test_review_probe_execution import probe_case  # noqa: F401


def _registry(cap):
    return ToolRegistry([cap.tool, subagent_note_read_tool,
                         *([cap.read_tool] if hasattr(cap, "read_tool") else [])])


def _case():
    assignment, store, channel, _submit, _kwargs = _bound(build=False)
    ledger = ReviewerProbeLedger()
    calls = []

    def execute(probe, context):
        assert context._subagent_mailbox is channel
        calls.append(probe)
        result = _receipt(probe, status="failed", exit_code=1)
        result.update(sourceRef=review_probe_source_artifact_id(probe.probe_id),
                      receiptRef=f"artifact_recovery_receipt_{len(calls)}")
        store.publish(result["sourceRef"], probe.to_dict(), authority=store._scheduler_authority)
        store.publish(result["receiptRef"], result, authority=store._scheduler_authority)
        return result

    cap = ReviewerCheckCapability(assignment=assignment, channel=channel, ledger=ledger,
                                  patch_digest=PATCH, run_probe=execute)
    return cap, ledger, _registry(cap), ToolContext(_subagent_mailbox=channel), store, calls


def _page(registry, context, **request):
    result = registry.execute("review_probe_read", request, context)
    assert result.ok, result.output
    return json.loads(result.output)


def _text(registry, context, **request):
    chunks, offset, digest = [], 0, None
    while True:
        page = _page(registry, context, **request, offset=offset, limit=137)
        assert page["offset"] == offset
        assert len(page["text"]) <= 137
        chunks.append(page["text"])
        digest = digest or page["digest"]
        assert digest == page["digest"]
        if page["nextOffset"] is None:
            break
        assert page["nextOffset"] > offset
        offset = page["nextOffset"]
    text = "".join(chunks)
    assert len(text) == page["totalChars"]
    assert "sha256:" + hashlib.sha256(text.encode()).hexdigest() == digest
    return text


def test_current_reviewer_recovers_published_full_multibyte_source_and_receipt():
    cap, ledger, registry, context, store, calls = _case()
    code = "# 公共场景🙂\n" * 1900 + "def test_required():\n    assert 1 == 2\n"
    result = registry.execute("review_test", {"criterionIds": ["ac_behavior"],
        "scenario": "a long public source", "code": code}, context)
    assert not result.ok
    receipt = json.loads(result.output)
    assert store.read(receipt["sourceRef"]) is not None
    assert not registry.execute("subagent_note_read", {"key": receipt["sourceRef"]}, context).ok
    listing = _page(registry, context, action="list")
    assert listing["probes"][0]["probeId"] == receipt["probeId"]
    assert listing["probes"][0]["currentStatus"] == "failed"
    assert listing["probes"][0]["receiptCount"] == 1
    assert _text(registry, context, action="source", probeId=receipt["probeId"]) == code
    recovered = json.loads(_text(registry, context, action="receipt", probeId=receipt["probeId"]))
    assert recovered == ledger.current_receipts(PATCH)[0]
    assert recovered["evidence"]["exitCode"] == 1
    assert receipt["recovery"]["tool"] == "review_probe_read"
    assert receipt["recovery"]["source"] == {"action": "source", "probeId": receipt["probeId"]}
    assert len(calls) == 1
    with pytest.raises(ValueError, match="all retained"):
        ledger.require_all_current_passed(PATCH)
    assert cap.read_tool.capability_profile.effects.read_only is True


@pytest.mark.parametrize("state", ["wrong-owner", "closed", "channel-closed", "revoked"])
def test_reader_keeps_exact_live_owner_boundary(state):
    cap, _ledger, registry, context, store, calls = _case()
    if state == "wrong-owner":
        context = replace(context, _subagent_mailbox=object())
    elif state == "closed":
        cap.close()
    elif state == "channel-closed":
        context._subagent_mailbox.close()
    else:
        store.revoke_pending_assignment_writes(authority=store._scheduler_authority)
    result = registry.execute("review_probe_read", {"action": "list"}, context)
    assert not result.ok and not calls


@pytest.mark.parametrize("read_input", [
    {"action": "source", "probeId": "review_probe_" + "b" * 24},
    {"action": "list", "artifactRef": "artifact_private"},
    {"action": "list", "probeId": "review_probe_unknown"},
    {"action": "source"}, {"action": "list", "offset": -1},
    {"action": "list", "offset": True}, {"action": "list", "limit": 4001},
])
def test_reader_rejects_unknown_or_nonledger_authority_and_invalid_offsets(read_input):
    _cap, _ledger, registry, context, _store, calls = _case()
    result = registry.execute("review_probe_read", read_input, context)
    assert not result.ok and not calls


@pytest.mark.parametrize("control", ["abandoned", "deadline", "cancel"])
def test_reader_honors_owner_control_before_returning_data(control):
    cap, _ledger, _registry_value, context, _store, _calls = _case()
    if control == "abandoned":
        event = threading.Event()
        event.set()
        context = replace(context, _tool_abandoned=event)
        expected = ToolExecutionAbandoned
    elif control == "deadline":
        context = replace(context, _task_deadline_monotonic=time.monotonic() - 1)
        expected = TaskDeadlineExceeded
    else:
        token = TurnCancellationToken("turn_" + "d" * 32)
        token.request()
        context = replace(context, _cancellation_token=token)
        expected = TurnCancellationRequested
    reader = getattr(cap, "read_tool", None)
    assert reader is not None, "The owner-bound reader must be registered."
    with pytest.raises(expected):
        reader.run(reader.validator({"action": "list"}), context)


def test_real_correction_retains_readable_original_receipts_and_correction(correction_case):  # noqa: F811
    cap, ledger, _old_registry, context, _store, attempts, publications, calls = correction_case
    registry = _registry(cap)
    old = json.loads(registry.execute("review_test", _input(), context).output)
    invalid = _correction(old)
    invalid["code"] = invalid["code"].replace("value == 3", "value == 2")
    rejected = registry.execute("review_test", invalid, context)
    assert not rejected.ok
    assert "review_probe_read" in rejected.output and old["probeId"] in rejected.output
    assert len(calls) == 1
    corrected = registry.execute("review_test", _correction(old), context)
    assert corrected.ok, corrected.output
    new = json.loads(corrected.output)
    directory = _page(registry, context, action="list", limit=1)
    assert directory["probes"][0]["retired"] is True and directory["nextOffset"] == 1
    second = _page(registry, context, action="list", offset=1, limit=1)
    assert second["probes"][0]["probeId"] == new["probeId"] and second["nextOffset"] is None
    assert _text(registry, context, action="source", probeId=old["probeId"]) == _input()["code"]
    assert json.loads(_text(registry, context, action="receipt", probeId=old["probeId"]))["status"] == "failed"
    document = json.loads(_text(registry, context, action="correction", probeId=old["probeId"]))
    assert document == publications[0]
    assert len(attempts) == 2 and len(ledger.probes()) == 1
    assert not registry.execute("review_probe_read", {"action": "receipt", "probeId": old["probeId"],
        "recordIndex": 1}, context).ok


def test_task_entry_registers_reader_in_same_reviewer_and_preserves_negative_verdict(tmp_path, monkeypatch):
    from minicode import subagent_model_routing
    from minicode.agent_budget import AgentTurnBudget
    from minicode.multi_agent_contracts import parse_role_result
    from minicode.tools import task as task_module
    from minicode.types import AgentStep, ModelUsage

    assignment, store, channel, submit, _kwargs = _bound()
    ledger = ReviewerProbeLedger()
    cap = ReviewerCheckCapability(assignment=assignment, channel=channel, ledger=ledger,
        patch_digest=PATCH, run_probe=lambda probe, _context: _receipt(probe, status="failed", exit_code=1))
    source = "def test_required():\n    assert 1 == 2\n"
    requests, registries, tool_failures = [], [], []

    class Model:
        def next(self, messages, **_kwargs):
            requests.append(messages)
            number = len(requests)
            if number == 1:
                call = ("review_test", {"criterionIds": ["ac_behavior"], "scenario": "public evidence", "code": source})
            elif number == 2:
                call = ("review_probe_read", {"action": "list"})
            elif number == 3:
                result = next(m for m in reversed(messages) if m.get("toolUseId") == "recovery-2")
                if result["content"].startswith("Unknown tool:"):
                    # Historical production delivers the actual missing tool
                    # error. Finish honestly, then assert recovery was absent;
                    # do not turn it into a scripted-model parsing exception.
                    tool_failures.append(result["content"])
                    call = ("review_submit", _rejection())
                else:
                    directory = json.loads(result["content"])
                    call = ("review_probe_read", {"action": "source", "probeId": directory["probes"][0]["probeId"]})
            else:
                assert number == 4
                result = next(m for m in reversed(messages) if m.get("toolUseId") == "recovery-3")
                assert json.loads(result["content"])["text"] == source
                call = ("review_submit", _rejection())
            return AgentStep(type="tool_calls", calls=[{"id": f"recovery-{number}", "toolName": call[0], "input": call[1]}],
                             usage=ModelUsage(input_tokens=100, output_tokens=50, source="estimated"))

    model = Model()

    def factory(*_args, **kwargs):
        registries.append(kwargs["tools"])
        return model

    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", factory)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k: ToolRegistry([]))
    budget = AgentTurnBudget(max_model_calls=10, max_total_tokens=500000)
    context = ToolContext(cwd=str(tmp_path), _runtime={"model": "fixture", "maxOutputTokens": 8192},
        _agent_budget=budget, _subagent_mailbox=channel, _logical_agent_role="reviewer",
        _logical_agent_scope=assignment.scope, _logical_agent_workspace_root=str(tmp_path),
        _logical_agent_max_model_calls=10)
    task_module._run({"agent_type": "plan", "description": "Inspect the retained public evidence",
        "prompt": "Review the supplied public criteria.", "_review_submit_tool": submit,
        "_review_check_capability": cap}, context)
    assert not tool_failures, f"The same Reviewer received an unavailable recovery tool: {tool_failures}"
    assert len(registries) == 1 and "review_probe_read" in registries[0].list_all()
    assert len(requests) == 4
    assert parse_role_result(assignment, json.loads(store.read(channel.write_key).content))["verdict"] == "changes_required"
    assert budget.snapshot().used_model_calls == 4 and budget.snapshot().used_total_tokens == 600
