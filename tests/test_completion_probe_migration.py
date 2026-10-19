"""Legacy purpose migration is independent, read-only and evidence-bound."""

import copy
import io
import json
from pathlib import Path

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.completion_probe_migration import CompletionProbeMigration
from minicode.deterministic_verifier import VerificationCheck
from minicode.implementation_completion_review import requirement_units
from minicode.implementation_execution import ImplementationSpec, implementation_candidate_digest
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.permissions import PermissionDeniedError
from minicode.reviewer_verification import ReviewerProbe
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken


OBJECTIVE = "1. reserve subtracts quantity.\n2. return_stock adds quantity.\n"
SOURCE = "def reserve(n, amount):\n    return n - amount\n\ndef return_stock(n, amount):\n    return n + amount\n"
DIAGNOSTIC = "import stock\ndef test_missing_return_api():\n    assert not hasattr(stock, 'return_stock')\n"
ACCEPTANCE = "from stock import reserve, return_stock\ndef test_quantities():\n    assert reserve(7, 2) == 5\n    assert return_stock(5, 2) == 7\n"


def fixture(tmp_path):
    (tmp_path / "stock.py").write_text(SOURCE)
    (tmp_path / "test_public.py").write_text("def test_public():\n    assert True\n")
    spec = ImplementationSpec(("stock.py", "test_public.py"), (VerificationCheck(
        "public", "tests", ("python3", "-m", "pytest", "-q", "test_public.py"), 20),), OBJECTIVE)
    ids = [unit["id"] for unit in requirement_units(OBJECTIVE)]
    probe = ReviewerProbe.create(criterion_ids=ids, scenario="Observe whether the new return API is missing",
                                 code=DIAGNOSTIC).to_dict()
    state = {"objective": OBJECTIVE, "probes": [probe], "retiredProbeIds": [],
        "receipts": [{"candidateDigest": "sha256:" + "a" * 64, "probeId": probe["probeId"],
                      "receipt": {"status": "passed", "sourceDigest": probe["sourceDigest"]}}],
        "reviews": [{"status": "changes_required", "findings": ["The new return API is missing."]}],
        "corrections": []}
    row = {"probeId": probe["probeId"], "purpose": "diagnostic",
        "reason": "This assertion succeeds when the requested API is absent; it diagnoses the old defect.",
        "objectiveEvidence": [{"startLine": 2, "endLine": 2}],
        "probeEvidence": [{"startLine": 3, "endLine": 3}],
        "acceptanceProbes": [{"criterionIds": ids, "scenario": "Requested stock operations work",
                               "code": ACCEPTANCE}]}
    return spec, state, row


def run_migration(tmp_path, monkeypatch, driver, *, state=None, token=None, budget=None, sink=None):
    spec, original, row = fixture(tmp_path)
    state = original if state is None else state
    model = object()
    tools = ToolRegistry([read_file_tool, edit_file_tool])
    owner = CompletionProbeMigration(spec)

    def loop(**values):
        assert values["model"] is model
        assert values["agent_budget"] is budget
        assert values["implementation_spec"] is None
        assert values["implementation_task"] is False
        assert values["max_steps"] is None
        assert values["enable_implementation_phases"] is False
        assert values["cwd"] != str(tmp_path)
        assert not {"edit_file", "write_file", "task", "run_command", "review_test"} & set(values["tools"].list_all())
        return driver(values, row)

    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", loop)
    return owner.assess(legacy_state=state, model=model, tools=tools,
        context=ToolContext(cwd=str(tmp_path)), agent_budget=budget,
        event_sink=sink, cancellation_token=token, deadline_monotonic=None)


def submit(values, rows):
    return values["tools"].execute("completion_probe_migration_submit", {"decisions": rows},
        ToolContext(cwd=values["cwd"], permissions=values["permissions"]))


@pytest.mark.parametrize("runtime_parent_override", [False, True])
def test_real_openai_wire_corrects_invalid_native_submission_with_same_adapter_and_budget(
        tmp_path, monkeypatch, runtime_parent_override):
    spec, state, row = fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    original_state = copy.deepcopy(state)
    parent = ToolRegistry([read_file_tool, edit_file_tool])
    runtime = {"model": "gpt-test", "openaiBaseUrl": "https://openai.invalid/v1",
        "openaiApiKey": "test", "modelMaxRetries": 0, "maxOutputTokens": 4096}
    if runtime_parent_override:
        runtime["_openrouter_params"] = {"tools": [{"type": "function", "function": {
            "name": "parent_write", "parameters": {"type": "object", "properties": {}}}}],
            "tool_choice": {"type": "function", "function": {"name": "parent_write"}}}
    adapter = OpenAIModelAdapter(runtime, parent)
    initial_schema = copy.deepcopy(adapter.request_tool_schemas())
    requests = []
    expected = {"read_file", "completion_probe_migration_read", "completion_probe_migration_submit",
                "completion_probe_migration_record"}

    class Response(io.BytesIO):
        status = 200

    def transport(request, *, timeout):
        del timeout
        body = json.loads(request.data.decode())
        requests.append(body)
        assert {t["function"]["name"] for t in body.get("tools", [])} == expected
        assert "tool_choice" not in body
        assert body["model"] == "gpt-test"
        assert len(requests) <= 2, "Accepted native migration must terminate this nested loop."
        if len(requests) == 2:
            previous = next(m["content"] for m in reversed(body["messages"]) if m.get("role") == "tool")
            assert "probe_migration_invalid" in previous
            assert "exactly once" in previous
        message = {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"migration-call-{len(requests)}", "type": "function", "function": {
                "name": "completion_probe_migration_submit",
                "arguments": json.dumps({"decisions": [] if len(requests) == 1 else [row]})}}]}
        return Response(json.dumps({"choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}}).encode())

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    budget = AgentTurnBudget()
    context = ToolContext(cwd=str(tmp_path), _runtime=runtime)
    result = CompletionProbeMigration(spec).assess(legacy_state=state, model=adapter,
        tools=parent, context=context, agent_budget=budget, event_sink=None,
        cancellation_token=None, deadline_monotonic=None)
    assert result["status"] == "resolved"
    assert len(requests) == 2
    assert budget.snapshot().used_model_calls == 2
    assert budget.snapshot().limit_total_tokens is None
    resolution = result["resolution"]
    assert resolution["candidateDigest"] == implementation_candidate_digest(spec, context)
    assert resolution["decisions"][0]["sourceDigest"] == state["probes"][0]["sourceDigest"]
    assert resolution["decisions"][0]["purpose"] == "diagnostic"
    evidence = json.loads(Path(resolution["reviewEvidenceRef"]).read_text())
    assert evidence["decisions"][0]["objectiveEvidence"] == row["objectiveEvidence"]
    authority = json.loads(Path(evidence["authorityRef"]).read_text())
    assert authority["originalObjective"] == OBJECTIVE
    assert authority["legacyState"] == original_state
    assert state == original_state
    assert adapter.tools is parent and adapter.request_tool_schemas() == initial_schema
    assert {name: (tmp_path / name).read_bytes() for name in before} == before


@pytest.mark.parametrize("invalid", ["missing_id", "duplicate_id", "foreign_id", "bad_objective_line",
                                     "bad_probe_line", "missing_criteria", "same_source", "acceptance_replacement"])
def test_invalid_classification_cannot_become_a_host_resolution(tmp_path, monkeypatch, invalid):
    def driver(values, row):
        bad = copy.deepcopy(row)
        rows = [bad]
        if invalid == "missing_id":
            rows = []
        elif invalid == "duplicate_id":
            rows.append(copy.deepcopy(bad))
        elif invalid == "foreign_id":
            bad["probeId"] = "review_probe_" + "0" * 24
        elif invalid == "bad_objective_line":
            bad["objectiveEvidence"][0]["endLine"] = 999
        elif invalid == "bad_probe_line":
            bad["probeEvidence"][0]["startLine"] = 0
        elif invalid == "missing_criteria":
            bad["acceptanceProbes"][0]["criterionIds"] = bad["acceptanceProbes"][0]["criterionIds"][:1]
        elif invalid == "same_source":
            bad["acceptanceProbes"][0]["code"] = DIAGNOSTIC
        elif invalid == "acceptance_replacement":
            bad["purpose"] = "acceptance"
        rejected = submit(values, rows)
        assert not rejected.ok
        assert json.loads(rejected.output)["errorCode"] == "probe_migration_invalid"
        assert not rejected._implementation_submitted
        accepted = submit(values, [row])
        assert accepted.ok and accepted._implementation_submitted

    assert run_migration(tmp_path, monkeypatch, driver)["status"] == "resolved"


def test_declared_acceptance_and_retired_sources_cannot_be_relabelled(tmp_path, monkeypatch):
    spec, state, row = fixture(tmp_path)
    legacy_id = row["probeId"]
    acceptance = ReviewerProbe.create(criterion_ids=state["probes"][0]["criterionIds"],
        scenario="Stock quantities", code=ACCEPTANCE).to_dict()
    retired = ReviewerProbe.create(criterion_ids=acceptance["criterionIds"],
        scenario="Retired historical check", code=ACCEPTANCE + "\n# earlier fixture\n").to_dict()
    state["probes"].extend([acceptance, retired])
    state["probePurposes"] = {acceptance["probeId"]: {"purpose": "acceptance"}}
    state["retiredProbeIds"] = [retired["probeId"]]

    def driver(values, row):
        packet = json.loads(values["messages"][1]["content"])
        assert [p["probeId"] for p in packet["legacyProbes"]] == [legacy_id]
        forged = {**row, "probeId": acceptance["probeId"]}
        assert not submit(values, [forged]).ok
        assert submit(values, [row]).ok

    result = run_migration(tmp_path, monkeypatch, driver, state=state)
    assert [d["probeId"] for d in result["resolution"]["decisions"]] == [legacy_id]


def test_exact_history_readonly_permissions_and_parent_lifecycle_isolation(tmp_path, monkeypatch):
    events = []

    class Sink:
        def emit(self, kind, **kwargs):
            events.append((kind, kwargs))

    def driver(values, row):
        context = ToolContext(cwd=values["cwd"], permissions=values["permissions"])
        with pytest.raises(PermissionDeniedError):
            values["permissions"].ensure_path_access(str(Path(values["cwd"]) / "stock.py"), "write")
        with pytest.raises(PermissionDeniedError):
            values["permissions"].ensure_path_access(str(Path(values["cwd"]) / ".env"), "read")
        offset, chunks = 0, []
        while offset is not None:
            result = values["tools"].execute("completion_probe_migration_read",
                {"probeId": row["probeId"], "offset": offset, "limit": 101}, context)
            assert result.ok
            page = json.loads(result.output)
            chunks.append(page["content"])
            offset = page["nextOffset"]
        history = json.loads("".join(chunks))
        assert history["source"]["code"] == DIAGNOSTIC
        assert history["receipts"][0]["receipt"]["status"] == "passed"
        values["event_sink"].emit("task.outcome", payload={"completionSucceeded": True})
        values["event_sink"].emit("model.completed", payload={"toolCallCount": 1, "privateText": "excluded"})
        assert submit(values, [row]).ok

    assert run_migration(tmp_path, monkeypatch, driver, sink=Sink())["status"] == "resolved"
    activity = [event for event in events if event[0] == "implementation.completion_review_activity"]
    assert len(activity) == 1 and "privateText" not in activity[0][1]["payload"]
    assert {kind for kind, _ in events} <= {"implementation.completion_review_activity",
        "implementation.completion_migration_progress", "implementation.completion_migration_checkpoint"}


def test_plain_final_and_candidate_change_never_queue_a_resolution(tmp_path, monkeypatch):
    result = run_migration(tmp_path, monkeypatch, lambda values, row: [])
    assert result["status"] == "inconclusive" and "resolution" not in result

    def changed(values, row):
        assert submit(values, [row]).ok
        (tmp_path / "stock.py").write_text(SOURCE + "\n# changed concurrently\n")

    result = run_migration(tmp_path, monkeypatch, changed)
    assert result["status"] == "inconclusive" and "resolution" not in result


@pytest.mark.parametrize("control", ["cancel", "budget"])
def test_cancellation_and_budget_control_flow_are_not_converted_to_resolution(tmp_path, monkeypatch, control):
    token = TurnCancellationToken("turn_" + "c" * 32)
    budget = AgentTurnBudget()

    def driver(values, row):
        if control == "budget":
            raise AgentBudgetExceeded("test exhaustion", budget.snapshot())
        token.request()
        return submit(values, [row])

    expected = AgentBudgetExceeded if control == "budget" else TurnCancellationRequested
    with pytest.raises(expected):
        run_migration(tmp_path, monkeypatch, driver, token=token, budget=budget)


def test_source_digest_or_objective_tampering_fails_before_model_call(tmp_path, monkeypatch):
    spec, state, _ = fixture(tmp_path)
    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", lambda **kwargs: pytest.fail("model called"))
    owner = CompletionProbeMigration(spec)
    for bad in [dict(state, objective="A different request."), copy.deepcopy(state)]:
        if bad["objective"] == OBJECTIVE:
            bad["probes"][0]["code"] += "\n# altered without identity update\n"
        with pytest.raises(ValueError):
            owner.assess(legacy_state=bad, model=object(), tools=ToolRegistry([]),
                context=ToolContext(cwd=str(tmp_path)), agent_budget=None, event_sink=None,
                cancellation_token=None, deadline_monotonic=None)
