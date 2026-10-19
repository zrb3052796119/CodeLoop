"""Real pytest and provider boundaries keep large host evidence out of handoff.

Only provider decisions are scripted.  The existing parent, independent review,
failure attribution, checkpoint authentication, evidence tools and isolated test
execution are production code.  These tests stop once continuation is observed;
they do not pretend that a scripted provider establishes delivery quality.
"""

from copy import deepcopy
import io
import json
from pathlib import Path
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_completion_review import requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.implementation_execution import ImplementationSpec
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.permissions import PermissionManager
from minicode.pytest_execution_evidence import validate_execution_report
from minicode.review_evidence_documents import canonical, content_digest
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool


OBJECTIVE = "1. hold subtracts the amount.\n2. release adds the amount."
PROBE = ("from stock import hold, release\ndef test_both():\n"
         "    assert hold(10, 3) == 7\n    assert release(7, 3) == 10\n")
RETAINED = "\n# Existing implementation work must survive the failed review.\n"


def _call(identity, name, arguments):
    return {"id": identity, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments)}}


def _result(body, identity):
    return next(message for message in body["messages"]
                if message.get("role") == "tool" and message.get("tool_call_id") == identity)


def _tool_json(body, identity):
    # The agent loop may append an operational tool-feedback note after a
    # complete JSON value.  A cut-off JSON value still fails raw_decode.
    return json.JSONDecoder().raw_decode(_result(body, identity)["content"])[0]


def _packet(body, key):
    for message in body["messages"]:
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str) and content.startswith("{"):
            value = json.loads(content)
            if key in value:
                return value
    raise AssertionError(f"Provider request has no {key} packet")


def _decision(body):
    content = next(message["content"] for message in reversed(body["messages"])
        if "\nCompletion evidence:\n" in (message.get("content") or ""))
    return json.loads(content.split("\nCompletion evidence:\n", 1)[1]), content


def _execution_view(value):
    """A full wrapper and a compact receipt index have the same evidence ref."""
    receipt = value.get("receipt", value)
    report = receipt.get("testReport", receipt)
    return report["executionReport"]


class _NativeEvidenceReads:
    """Read every byte through advertised native calls, then two focused views."""

    def __init__(self, original, document_ref, stop):
        self.original = original
        self.document_ref = document_ref
        assertion = original["cases"][0]["assertions"][0]["assertionId"]
        self.requests = [
            {"documentId": document_ref},
            {"documentId": document_ref, "filters": {
                "consumer": {"path": "stock.py", "qualname": "probe_helper_179"}}},
            {"documentId": document_ref, "filters": {"assertionId": assertion}},
        ]
        self.stop = stop
        self.active = None
        self.parts = []
        self.completed = []
        self.page_count = 0

    def step(self, body, names):
        assert "completion_evidence_read" in names
        if self.active is not None:
            page = _tool_json(body, self.active)
            assert page.get("displayOnly") is True
            assert "completeDelivered" not in page, "Display reads cannot acquire audit approval credit."
            assert len(page["text"]) <= 8000
            assert page["offset"] == sum(map(len, self.parts))
            self.parts.append(page["text"])
            if page["nextCursor"] is not None:
                return self._call({"cursor": page["nextCursor"]})
            document = json.loads("".join(self.parts))
            self.completed.append(document)
            self.parts = []
            self.active = None
        if self.requests:
            return self._call(self.requests.pop(0))
        assert self.completed[0] == self.original
        selected = self.completed[1]
        assert selected["originalDocumentRef"] == self.document_ref
        assert selected["originalDigest"] == content_digest(canonical(self.original))
        calls = selected["cases"][0]["calls"]
        assert {row["qualname"] for row in calls} == {"test_both", "release", "probe_helper_179"}
        assert all(row["identityVerified"] for row in calls)
        selected_assertions = self.completed[2]["cases"][0]
        assert len(selected_assertions["assertions"]) == 1 and selected_assertions["calls"] == []
        assert self.page_count > 3, "The complete original must actually require multiple tool pages."
        raise self.stop

    def _call(self, arguments):
        self.page_count += 1
        self.active = f"host-evidence-{self.page_count}"
        return _call(self.active, "completion_evidence_read", arguments)


@pytest.fixture
def failed_native_handoff(tmp_path, monkeypatch, request):
    import minicode.workflow_runner as runner

    read_before_interrupt = getattr(request, "param", True)
    # Many real candidate functions make the trace large without invented
    # evidence, huge stdout, or executing project-specific benchmark material.
    initial = ("def hold(n, amount):\n    return n - amount\n\n"
               "def release(n, amount):\n"
               + "".join(f"    probe_helper_{i}(n)\n" for i in range(180))
               + "    return n\n\n"
               + "".join(f"def probe_helper_{i}(n):\n    return n\n\n" for i in range(180)))
    (tmp_path / "stock.py").write_text(initial)
    (tmp_path / "test_public.py").write_text(
        "from stock import hold\ndef test_hold():\n    assert hold(10, 3) == 7\n")
    commands, checkpoints, events, requests = [], [], [], []

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **_kwargs):
            commands.append({"argv": list(argv), "cwd": Path(cwd)})
            return argv

    class Sink:
        def emit(self, name, *, step=None, payload=None):
            events.append({"type": name, "payload": deepcopy(payload)})

    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
        lambda *_a, **_k: DeterministicVerifier(sandbox_backend=Sandbox(),
            explicit_trusted_executables={"python3": sys.executable}))
    registry = ToolRegistry([read_file_tool, write_file_tool])
    runtime = {"model": "gpt-test", "openaiBaseUrl": "https://openai.invalid/v1",
        "openaiApiKey": "test", "modelMaxRetries": 0, "maxOutputTokens": 4096}
    adapter = OpenAIModelAdapter(runtime, registry)
    spec = ImplementationSpec(("stock.py", "test_public.py"), (VerificationCheck(
        "public", "tests", ("python3", "-m", "pytest", "-q", "test_public.py"), 20),), OBJECTIVE)
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]
    options = dict(model=adapter, tools=registry, runtime=runtime, cwd=str(tmp_path),
        implementation_task=True, implementation_spec=spec, enable_work_chain=False,
        enable_implementation_phases=False, max_steps=None, agent_budget=AgentTurnBudget(),
        event_sink=Sink(), defer_curator_maintenance=True,
        implementation_checkpoint_sink=lambda document, messages: checkpoints.append(
            (deepcopy(document), deepcopy(messages))))
    stop = KeyboardInterrupt("Observed intact same-owner repair context and native evidence access")
    counts = {"parent": 0, "reviewer": 0, "adjudicator": 0}
    observed = {}

    class Response(io.BytesIO):
        status = 200

    def transport(request, *, timeout):
        body = json.loads(request.data)
        names = {tool["function"]["name"] for tool in body["tools"]}
        role = ("adjudicator" if "completion_probe_adjudication_submit" in names else
                "reviewer" if "completion_review_submit" in names else "parent")
        assert "completion_coverage_audit_submit" not in names, "A confirmed missing requirement cannot enter approval."
        counts[role] += 1
        assert sum(counts.values()) < 45, "Evidence reading must not restart the review protocol."
        requests.append({"role": role, "body": deepcopy(body)})
        chosen = None
        text = None
        if role == "parent":
            assert {"write_file", "executor_check"} <= names
            if counts[role] == 1:
                chosen = _call("existing-edit", "write_file", {"path": "stock.py", "content": initial + RETAINED})
            elif counts[role] == 2:
                observed["prior_write"] = deepcopy(_result(body, "existing-edit"))
                text = "Both requested behaviors are complete."
            else:
                assert _result(body, "existing-edit") == observed["prior_write"]
                if "reader" not in observed:
                    decision, feedback = _decision(body)
                    original = next(event["payload"] for event in reversed(events)
                        if event["type"] == "implementation.completion_decided")
                    assert decision["status"] == "changes_required" and decision["continuesSameExecutor"]
                    assert decision["executorId"] == original["executorId"]
                    receipt = original["probeReceipts"][0]["receipt"]
                    report = receipt["testReport"]["executionReport"]
                    assert validate_execution_report(receipt["testReport"])
                    assert len(canonical(report)) > 60000
                    assert len(feedback) < len(canonical(original)) // 3
                    assert len(request.data) < len(canonical(report))
                    assert "assert 7 == 10" in feedback, "The concrete assertion failure must remain actionable."
                    assert decision["repairProbes"][0]["code"] == PROBE
                    view = _execution_view(decision["probeReceipts"][0])
                    assert view["displayOnly"] and "cases" not in view
                    assert view["originalDigest"] == content_digest(canonical(report))
                    observed.update(decision=original, feedback_view=decision,
                        reader=_NativeEvidenceReads(report, view["documentRef"], stop))
                    if not read_before_interrupt:
                        raise stop
                chosen = observed["reader"].step(body, names)
        elif role == "reviewer":
            assert not {"write_file", "executor_check"} & names
            if counts[role] == 1:
                chosen = _call("independent-check", "review_test", {"purpose": "acceptance", "criterionIds": ids,
                    "scenario": "Both original quantity operations are exercised.", "code": PROBE})
            else:
                assert counts[role] == 2
                value = _tool_json(body, "independent-check")
                assert value["receipt"]["status"] == "failed"
                view = _execution_view(value)
                assert view["displayOnly"] and "cases" not in view
                assert "assert 7 == 10" in canonical(value)
                chosen = _call("reject-gap", "completion_review_submit", {"status": "changes_required",
                    "requirements": [{"id": identity, "status": "missing", "probeIds": [],
                        "sourceEvidence": [{"path": "stock.py", "startLine": 4, "endLine": 185}],
                        "rationale": "release returns the original stock instead of adding the amount."} for identity in ids],
                    "findings": ["release(7, 3) must add the amount; the test reports assert 7 == 10."]})
        else:
            assert not {"write_file", "executor_check", "completion_review_submit"} & names
            packet = _packet(body, "originalObjective")
            assert packet["originalObjective"] == OBJECTIVE and packet["probe"]["code"] == PROBE
            assert _execution_view(packet["receipt"])["displayOnly"]
            assert len(request.data) < 30000
            if counts[role] == 1:
                chosen = _call("attribution-source", "read_file", {"path": "stock.py"})
            else:
                assert counts[role] == 2
                assert "return n" in _result(body, "attribution-source")["content"]
                chosen = _call("product-gap", "completion_probe_adjudication_submit", {
                    "verdict": "product_gap", "reason": "release leaves n unchanged instead of adding the requested amount.",
                    "objectiveEvidence": [{"startLine": 2, "endLine": 2}],
                    "sourceEvidence": [{"path": "stock.py", "startLine": 4, "endLine": 185}]})
        assert chosen is None or chosen["function"]["name"] in names
        message = {"role": "assistant", "content": text}
        if chosen is not None:
            message["tool_calls"] = [chosen]
        return Response(json.dumps({"choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls" if chosen else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}).encode())

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    with pytest.raises(KeyboardInterrupt) as caught:
        run_agent_turn(messages=[{"role": "user", "content": OBJECTIVE}], **options)
    assert caught.value is stop
    assert counts["reviewer"] == counts["adjudicator"] == 2
    assert checkpoints and (tmp_path / "stock.py").read_text() == initial + RETAINED
    return locals()


def test_native_failed_review_returns_compact_feedback_and_exact_retrievable_evidence(failed_native_handoff):
    case = failed_native_handoff
    original = case["observed"]["decision"]
    document, saved = case["checkpoints"][-1]
    assert document["executorId"] == original["executorId"] and document["attempt"] == original["attempt"]
    assert document["reviewState"]["receipts"][0]["receipt"] == original["probeReceipts"][0]["receipt"]
    feedback = next(message for message in saved if message.get("_implementation_completion_feedback"))
    assert feedback.get("_completion_evidence_original")
    assert canonical(original) in feedback["_completion_evidence_original"]
    assert case["observed"]["reader"].completed[0] == original["probeReceipts"][0]["receipt"]["testReport"]["executionReport"]
    assert case["commands"] and all(command["cwd"] != case["tmp_path"]
        and not command["cwd"].exists() for command in case["commands"])
    assert not any(event["type"].startswith("subagent.") for event in case["events"])


@pytest.mark.parametrize("failed_native_handoff", [False], indirect=True)
def test_authenticated_legacy_feedback_restore_projects_before_provider_without_erasing_history(
        failed_native_handoff, monkeypatch):
    case = failed_native_handoff
    document, messages = deepcopy(case["checkpoints"][-1])
    # Recreate the old production representation from its actual host decision,
    # not from a display summary or a manufactured execution receipt.
    feedback = next(message for message in messages if message.get("_implementation_completion_feedback"))
    feedback["content"] = feedback["content"].split("\nCompletion evidence:\n", 1)[0] + \
        "\nCompletion evidence:\n" + canonical(case["observed"]["decision"])
    feedback.pop("_completion_evidence_original", None)
    feedback.pop("_completion_evidence_projection", None)
    captured = HostImplementationContinuation.capture(spec=case["spec"],
        context=ToolContext(cwd=str(case["tmp_path"])), messages=messages,
        executor_id=document["executorId"], attempt=document["attempt"], review_state=document["reviewState"])
    legacy_document = captured.document
    restored = HostImplementationContinuation.from_document(legacy_document, spec=case["spec"], messages=messages)
    before_messages, before_document = deepcopy(messages), deepcopy(legacy_document)
    old_feedback = deepcopy(feedback)
    # The checkpoint stopped on the original handoff, before any evidence page
    # was requested.  The restored reader must rebuild refs from host originals.
    restored_requests = []
    resumed = {}
    stop = KeyboardInterrupt("Observed authenticated restored parent request")

    def transport(request, *, timeout):
        body = json.loads(request.data)
        restored_requests.append(body)
        names = {tool["function"]["name"] for tool in body["tools"]}
        assert {"write_file", "executor_check", "completion_evidence_read"} <= names
        decision, content = _decision(body)
        assert decision["executorId"] == document["executorId"]
        assert decision["continuesSameExecutor"] and decision["status"] == "changes_required"
        assert len(content) < len(old_feedback["content"]) // 3
        assert "assert 7 == 10" in content
        assert _result(body, "existing-edit") == case["observed"]["prior_write"]
        assert not any("_completion_evidence_original" in row for row in body["messages"])
        if not resumed:
            original = case["observed"]["decision"]["probeReceipts"][0]["receipt"]["testReport"]["executionReport"]
            assert len(request.data) < len(canonical(original))
            view = _execution_view(decision["probeReceipts"][0])
            resumed["reader"] = _NativeEvidenceReads(original, view["documentRef"], stop)
        call = resumed["reader"].step(body, names)
        assert len(restored_requests) < 30
        return case["Response"](json.dumps({"choices": [{"index": 0, "message": {
            "role": "assistant", "content": None, "tool_calls": [call]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}).encode())

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    with pytest.raises(KeyboardInterrupt) as caught:
        run_agent_turn(messages=messages, implementation_continuation=restored, **case["options"])
    assert caught.value is stop and len(restored_requests) > 3
    assert messages == before_messages and legacy_document == before_document
    new_document, new_messages = case["checkpoints"][-1]
    assert new_document["executorId"] == document["executorId"]
    old_state, new_state = document["reviewState"], new_document["reviewState"]
    assert old_state["evidenceDirectory"] in new_state["priorEvidenceDirectories"]
    assert {key: value for key, value in new_state.items()
        if key not in {"evidenceDirectory", "priorEvidenceDirectories"}} == {
        key: value for key, value in old_state.items()
        if key not in {"evidenceDirectory", "priorEvidenceDirectories"}}
    projected = next(message for message in new_messages if message.get("_implementation_completion_feedback"))
    assert projected["_completion_evidence_original"] == old_feedback["content"]
    assert (case["tmp_path"] / "stock.py").read_text() == case["initial"] + RETAINED
