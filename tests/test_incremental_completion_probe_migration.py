"""Complete per-probe decisions survive an interrupted real adapter exchange."""

import copy
import io
import json
from pathlib import Path

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.completion_probe_migration import (
    CompletionProbeMigration, _digest, _json, validate_continuation_state,
)
from minicode.implementation_execution import implementation_candidate_digest
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.reviewer_verification import ReviewerProbe
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from test_completion_probe_migration import ACCEPTANCE, fixture


def two_probes(tmp_path):
    spec, state, first = fixture(tmp_path)
    probe = ReviewerProbe.create(criterion_ids=state["probes"][0]["criterionIds"],
        scenario="Existing acceptance behavior", code=ACCEPTANCE).to_dict()
    state["probes"].append(probe)
    second = {"probeId": probe["probeId"], "purpose": "acceptance",
        "reason": "Both assertions express the requested behavior after repair.",
        "objectiveEvidence": [{"startLine": 1, "endLine": 2}],
        "probeEvidence": [{"startLine": 3, "endLine": 4}], "acceptanceProbes": []}
    return spec, state, [first, second]


def invocation(tmp_path, spec, legacy, **extra):
    return dict(legacy_state=legacy, model=object(), tools=ToolRegistry([read_file_tool]),
        context=ToolContext(cwd=str(tmp_path)), agent_budget=AgentTurnBudget(),
        event_sink=None, cancellation_token=None, deadline_monotonic=None, **extra)


def native(values, name, arguments):
    return values["tools"].execute(name, arguments,
        ToolContext(cwd=values["cwd"], permissions=values["permissions"]))


def test_real_adapter_record_interrupt_restore_record_finalize(tmp_path, monkeypatch):
    spec, legacy, rows = two_probes(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    tools = ToolRegistry([read_file_tool, edit_file_tool])
    runtime = {"model": "gpt-test", "openaiBaseUrl": "https://invalid.example/v1",
               "openaiApiKey": "test", "modelMaxRetries": 0, "maxOutputTokens": 4096}
    adapter = OpenAIModelAdapter(runtime, tools)
    original_schema = copy.deepcopy(adapter.request_tool_schemas())
    requests, checkpoints = [], []
    expected = {"read_file", "completion_probe_migration_record",
                "completion_probe_migration_read", "completion_probe_migration_submit"}

    class Response(io.BytesIO):
        status = 200

    def transport(request, *, timeout):
        del timeout
        body = json.loads(request.data)
        requests.append(body)
        assert {t["function"]["name"] for t in body["tools"]} == expected
        number = len(requests)
        if number == 2:
            assert any(m.get("role") == "tool" and rows[0]["probeId"] in m.get("content", "")
                       for m in body["messages"])
            raise KeyboardInterrupt("interrupt after a complete saved decision")
        if number == 3:
            assert any(m.get("role") == "tool" and m.get("tool_call_id") == "record-first"
                       for m in body["messages"])
            assert any("hostMigrationContinuation" in m.get("content", "")
                       for m in body["messages"] if isinstance(m.get("content"), str))
            for retained in saved["state"]["messages"]:
                if retained.get("_task_ledger"):
                    # The adapter joins all system messages into one wire message.
                    assert any(retained["content"] in m.get("content", "")
                               for m in body["messages"] if isinstance(m.get("content"), str))
        name = "completion_probe_migration_submit" if number == 4 else "completion_probe_migration_record"
        args = {"finalize": True} if number == 4 else {"decision": rows[0 if number == 1 else 1]}
        identity = "record-first" if number == 1 else f"call-{number}"
        message = {"role": "assistant", "content": None, "tool_calls": [{"id": identity,
            "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
        return Response(json.dumps({"choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 100, "completion_tokens": 30}}).encode())

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    budget = AgentTurnBudget()
    arguments = invocation(tmp_path, spec, legacy, checkpoint_sink=checkpoints.append)
    arguments.update(model=adapter, tools=tools, context=ToolContext(cwd=str(tmp_path), _runtime=runtime),
                     agent_budget=budget)
    with pytest.raises(KeyboardInterrupt):
        CompletionProbeMigration(spec).assess(**arguments)
    saved = copy.deepcopy(checkpoints[-1])
    validated = validate_continuation_state(spec, legacy, saved,
        implementation_candidate_digest(spec, ToolContext(cwd=str(tmp_path))))
    assert len(validated["state"]["completedUnits"]) == 1
    assert any(m.get("role") == "tool_result" and m.get("toolUseId") == "record-first"
               for m in validated["state"]["messages"])
    old_messages = copy.deepcopy(saved["state"]["messages"])
    result = CompletionProbeMigration(spec).assess(**arguments, continuation_state=saved)
    assert result["status"] == "resolved" and len(result["resolution"]["decisions"]) == 2
    assert len(requests) == 4 and budget.snapshot().used_model_calls == 4
    assert saved["state"]["messages"] == old_messages
    assert len(result["continuationState"]["state"]["completedUnits"]) == 2
    assert result["continuationState"]["state"]["snapshotPath"] != saved["state"]["snapshotPath"]
    assert adapter.tools is tools and adapter.request_tool_schemas() == original_schema
    assert {name: (tmp_path / name).read_bytes() for name in before} == before


def saved_progress(tmp_path, monkeypatch, *, record=True):
    spec, legacy, rows = two_probes(tmp_path)
    checkpoints = []

    def loop(**values):
        if record:
            result = native(values, "completion_probe_migration_record", {"decision": rows[0]})
            assert result.ok and not result._implementation_submitted
            ledger = json.loads(result.output)
            assert ledger["remainingProbeIds"] == [rows[1]["probeId"]]
            assert Path(ledger["completedUnits"][0]["evidenceRef"]).is_file()
            assert not native(values, "completion_probe_migration_submit", {"finalize": True}).ok
            changed = {**rows[0], "reason": "overwrite a saved decision"}
            assert not native(values, "completion_probe_migration_record", {"decision": changed}).ok
            assert native(values, "completion_probe_migration_record", {"decision": rows[0]}).ok
        values["context_manager"].messages.append({"role": "user", "content": "actual retained observation"})
        raise KeyboardInterrupt("stop")

    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", loop)
    with pytest.raises(KeyboardInterrupt):
        CompletionProbeMigration(spec).assess(**invocation(tmp_path, spec, legacy,
            checkpoint_sink=checkpoints.append))
    return spec, legacy, rows, checkpoints[-1]


def test_zero_record_interruption_still_checkpoints_actual_messages(tmp_path, monkeypatch):
    spec, legacy, _rows, saved = saved_progress(tmp_path, monkeypatch, record=False)
    assert saved["state"]["completedUnits"] == []
    assert saved["state"]["messages"][-1]["content"] == "actual retained observation"
    assert validate_continuation_state(spec, legacy, saved) == saved


@pytest.mark.parametrize("tamper", ["message", "unit_hash", "duplicate", "foreign", "source",
    "objective", "candidate", "persisted_checkpoint", "persisted_unit", "symlink", "escape"])
def test_invalid_checkpoint_rejected_before_model(tmp_path, monkeypatch, tamper):
    spec, legacy, rows, saved = saved_progress(tmp_path, monkeypatch)
    bad = copy.deepcopy(saved)
    state = bad["state"]
    if tamper == "message":
        state["messages"][0]["content"] += " altered"
    elif tamper == "unit_hash":
        state["completedUnits"][0]["decisionDigest"] = "sha256:" + "0" * 64
    elif tamper == "duplicate":
        state["completedUnits"].append(copy.deepcopy(state["completedUnits"][0]))
    elif tamper == "foreign":
        state["completedUnits"][0]["decision"]["probeId"] = "review_probe_" + "0" * 24
    elif tamper == "source":
        state["sourceDigests"][rows[0]["probeId"]] = "sha256:" + "0" * 64
    elif tamper == "objective":
        state["objectiveDigest"] = "sha256:" + "0" * 64
    elif tamper == "candidate":
        state["candidateDigest"] = "sha256:" + "0" * 64
    elif tamper == "persisted_checkpoint":
        Path(bad["checkpointRef"]).write_text("{}")
    elif tamper == "persisted_unit":
        Path(state["completedUnits"][0]["evidenceRef"]).write_text("{}")
    elif tamper in {"symlink", "escape"}:
        path = Path(state["completedUnits"][0]["evidenceRef"])
        other = tmp_path / path.name
        other.write_bytes(path.read_bytes())
        if tamper == "symlink":
            path.unlink()
            path.symlink_to(other)
        else:
            state["completedUnits"][0]["evidenceRef"] = str(other)
    # Recompute the outer seal to exercise inner structure/bindings independently.
    if tamper != "persisted_checkpoint":
        bad["checkpointDigest"] = _digest(_json(state))
        Path(bad["checkpointRef"]).write_text(_json(state))
    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", lambda **_: pytest.fail("model called"))
    with pytest.raises(ValueError):
        CompletionProbeMigration(spec).assess(**invocation(tmp_path, spec, legacy), continuation_state=bad)


def test_invalid_later_row_keeps_prior_unit_and_compatible_final_works(tmp_path, monkeypatch):
    spec, legacy, rows = two_probes(tmp_path)

    def loop(**values):
        assert native(values, "completion_probe_migration_record", {"decision": rows[0]}).ok
        invalid = {**rows[1], "probeEvidence": [{"startLine": 999, "endLine": 999}]}
        assert not native(values, "completion_probe_migration_record", {"decision": invalid}).ok
        ledger = json.loads(native(values, "completion_probe_migration_read", {}).output)
        assert ledger["completedProbeIds"] == [rows[0]["probeId"]]
        assert ledger["remainingProbeIds"] == [rows[1]["probeId"]]
        assert native(values, "completion_probe_migration_submit", {"decisions": rows}).ok

    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", loop)
    result = CompletionProbeMigration(spec).assess(**invocation(tmp_path, spec, legacy))
    assert result["status"] == "resolved"
    assert len(result["continuationState"]["state"]["completedUnits"]) == 2


def test_progress_and_checkpoint_events_have_real_refs_counts_and_no_source_text(tmp_path, monkeypatch):
    spec, legacy, rows = two_probes(tmp_path)
    events = []

    class Sink:
        def emit(self, kind, *, step=None, payload=None):
            events.append((kind, payload))

    def loop(**values):
        for row in rows:
            assert native(values, "completion_probe_migration_record", {"decision": row}).ok
            assert native(values, "completion_probe_migration_record", {"decision": row}).ok
        values["context_manager"].messages.append({"role": "user", "content": "PRIVATE_MESSAGE_SENTINEL"})
        assert native(values, "completion_probe_migration_submit", {"finalize": True}).ok

    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", loop)
    arguments = invocation(tmp_path, spec, legacy)
    arguments["event_sink"] = Sink()
    result = CompletionProbeMigration(spec).assess(**arguments)
    progress = [payload for kind, payload in events if kind == "implementation.completion_migration_progress"]
    checkpoints = [payload for kind, payload in events if kind == "implementation.completion_migration_checkpoint"]
    assert [p["completedCount"] for p in progress] == [1, 2]
    assert [p["remainingCount"] for p in progress] == [1, 0]
    assert all(p["totalCount"] == 2 and Path(p["evidenceRef"]).is_file() for p in progress)
    assert len(checkpoints) == 1
    checkpoint = checkpoints[0]
    assert checkpoint["completedCount"] == 2 and checkpoint["remainingCount"] == 0
    assert checkpoint["messageCount"] == len(result["continuationState"]["state"]["messages"])
    assert checkpoint["checkpointRef"] == result["continuationState"]["checkpointRef"]
    assert checkpoint["checkpointDigest"] == result["continuationState"]["checkpointDigest"]
    assert set(checkpoint) == {"reviewId", "candidateDigest", "completedCount", "totalCount", "remainingCount",
                               "messageCount", "checkpointRef", "checkpointDigest"}
    assert all(set(p) == {"reviewId", "candidateDigest", "completedCount", "totalCount", "remainingCount", "evidenceRef"}
               for p in progress)
    serialized = json.dumps(events)
    assert "PRIVATE_MESSAGE_SENTINEL" not in serialized and ACCEPTANCE not in serialized
    assert spec.objective not in serialized


def test_inconclusive_feedback_only_exposes_checkpoint_reference(tmp_path, monkeypatch):
    spec, legacy, _rows = two_probes(tmp_path)
    captured = []

    def loop(**values):
        values["context_manager"].messages.append({"role": "user", "content": "PRIVATE_CHILD_CONTEXT"})
        return values["context_manager"].messages

    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", loop)
    result = CompletionProbeMigration(spec).assess(**invocation(tmp_path, spec, legacy,
        checkpoint_sink=captured.append))
    assert result["status"] == "inconclusive"
    assert result["continuationCheckpointRef"] == captured[-1]["checkpointRef"]
    assert "PRIVATE_CHILD_CONTEXT" not in json.dumps(result)
    assert captured[-1]["state"]["messages"][-1]["content"] == "PRIVATE_CHILD_CONTEXT"
