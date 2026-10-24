"""Coverage views use real pytest receipts and the native provider boundary."""

from copy import deepcopy
import json
import subprocess

import pytest

from minicode.completion_evidence_view import EvidenceViews
from minicode.coverage_audit_state import coverage_delivery_pairs, record_document, restore_evidence_documents
from minicode.review_evidence_documents import canonical
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from test_completion_coverage_audit import _case, _native
from test_coverage_audit_resume import run_audit


@pytest.fixture
def case(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    product = "def hold(n, amount):\n    return hop_0(n, amount)\n\ndef release(n, amount):\n    return n + amount\n"
    for index in range(80):
        expression = f"hop_{index + 1}(n, amount)" if index < 79 else "n - amount"
        product += f"\ndef hop_{index}(n, amount):\n    return {expression}\n"
    result = _case(tmp_path, monkeypatch, product=product)
    assert len(result["receipt"]["testReport"]["executionReport"]["cases"][0]["calls"]) > 80
    return result


def packet(body):
    packets = [json.loads(message["content"]) for message in body["messages"]
               if message["role"] == "user" and '"evidenceIndex"' in message["content"]]
    assert len(packets) == 1
    return packets[0]


def last_output(body):
    return json.JSONDecoder().raw_decode(next(message["content"] for message in reversed(body["messages"])
        if message["role"] == "tool"))[0]


def test_native_views_preserve_full_authority_without_receipt_tree_or_source_credit(case, monkeypatch):
    original = deepcopy(case["evidence"])
    selected = {}

    def filter_receipt(body):
        item = packet(body)["evidenceIndex"][0]
        selected.update(item)
        view = item["currentReceiptView"]
        assert view["displayOnly"] is True and view["status"] == "passed"
        assert view["executionReport"]["validation"] == "valid"
        assert "cases" not in view["executionReport"]
        assert '"callId"' not in canonical(packet(body))
        return "completion_evidence_read", {"documentId": view["executionReport"]["documentRef"],
            "filters": {"consumer": {"path": "stock.py", "qualname": "hold"}}}

    def submit_without_source(body):
        page = last_output(body)
        assert page["displayOnly"] is True and "completeDelivered" not in page
        assert page["nextCursor"] is None
        result = json.loads(page["text"])
        calls = result["cases"][0]["calls"]
        assert any(call["qualname"] == "hold" for call in calls)
        assert not any(call["qualname"].startswith("hop_") for call in calls)
        observed = case["receipt"]["testReport"]["executionReport"]["cases"][0]["calls"]
        assert all(call in observed for call in calls)
        return "completion_coverage_audit_submit", case["valid"]

    def read_source(body):
        assert "read the exact complete probe source" in last_output(body)["message"]
        return "review_evidence_read", {"documentId": selected["sourceDocumentId"]}

    requests = _native(monkeypatch, case, [filter_receipt, submit_without_source, read_source,
        ("completion_coverage_audit_submit", case["valid"])])
    result = run_audit(case)
    assert result["status"] == "approved"
    assert case["evidence"] == original
    authority = json.loads(next(path.read_text() for kind, path in case["saved"]
                               if kind == "coverage-audit-authority"))
    assert authority["probeEvidence"][case["probe"].probe_id] == original
    assert all('"callId"' not in message.get("content", "") for message in requests[0]["messages"])
    documents = json.loads(next(path.read_text() for kind, path in case["saved"]
                               if kind == "coverage-audit-evidence-documents"))["documents"]
    assert any(document["documentId"] == selected["currentReceiptDocumentId"]
               and '"executionReport"' in document["text"] for document in documents)


def test_native_original_pages_survive_resume_without_granting_audit_delivery(case, monkeypatch):
    states, observed = [], {}

    def read_original(body):
        view = packet(body)["evidenceIndex"][0]["currentReceiptView"]
        observed["ref"] = view["executionReport"]["documentRef"]
        return "completion_evidence_read", {"documentId": observed["ref"]}

    def interrupt(body):
        observed["firstPage"] = last_output(body)
        assert observed["firstPage"]["nextCursor"]
        raise KeyboardInterrupt("retain real displayed page and pending audit")

    _native(monkeypatch, case, [read_original, interrupt])
    with pytest.raises(KeyboardInterrupt):
        run_audit(case, sink=lambda value: states.append(deepcopy(value)))
    state = states[-1]
    retained = record_document(state["messagesRecord"])["messages"]
    assert coverage_delivery_pairs(state) == []
    store = restore_evidence_documents(state)
    assert store.missing([row["documentId"] for row in store.snapshot()])
    pages = []

    def resumed(body):
        if not pages:
            pages.append(None)
            return "completion_evidence_read", {"documentId": observed["ref"]}
        page = last_output(body)
        if pages == [None]:
            assert page["text"] == observed["firstPage"]["text"]
            pages.clear()
        pages.append(page)
        assert page["displayOnly"] is True and "completeDelivered" not in page
        if page["nextCursor"]:
            return "completion_evidence_read", {"cursor": page["nextCursor"]}
        return "completion_coverage_audit_submit", {"status": "inconclusive", "requirements": [],
            "findings": ["Source was not read; trace delivery alone cannot establish requirement coverage."]}

    requests = _native(monkeypatch, case, [resumed] * 30)
    # Actual continuation tool identities are distinct from the first turn.
    from minicode import openai_adapter
    original_transport = openai_adapter.open_verified_url

    def distinct_ids(request, **kwargs):
        response = original_transport(request, **kwargs)
        body = json.loads(response.getvalue())
        for call in body["choices"][0]["message"].get("tool_calls", []):
            call["id"] += "-continued"
        return type(response)(json.dumps(body).encode())

    monkeypatch.setattr(openai_adapter, "open_verified_url", distinct_ids)
    resumed_states = []
    result = run_audit(case, state=state, sink=lambda value: resumed_states.append(deepcopy(value)))
    assert result["status"] == "inconclusive" and len(requests) > 2
    assert json.loads("".join(page["text"] for page in pages)) == case["receipt"]["testReport"]["executionReport"]
    pending = next(value for value in resumed_states if value is not None)
    assert pending["documentsRecord"] == state["documentsRecord"]
    assert record_document(pending["messagesRecord"])["messages"][:len(retained)] == retained
    assert coverage_delivery_pairs(pending) == []


@pytest.mark.parametrize("projected_output", [False, True])
def test_native_challenge_owner_projection_stays_readable_through_coverage_registry(case, monkeypatch, projected_output):
    owner_views = EvidenceViews(candidate_digest=case["candidate_digest"])
    # A challenge response may attach host-owned metadata beyond the current
    # receipt. Its original must remain readable from the owner's exact store.
    original = {**case["wrapped"], "retainedChallenge": "owner metadata"}
    projected = owner_views.view(original)
    challenge = ToolDefinition(name="review_test", description="Return this retained real pytest challenge",
        input_schema={"type": "object"}, validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output=canonical(projected if projected_output else original)),
        capability_profile=case["read_probe"].capability_profile)
    case["tools"] = ToolRegistry([*case["tools"].list(), challenge, owner_views.tool_definition()])
    captured = {}

    def open_original(body):
        output = last_output(body)
        assert output == projected
        assert len(canonical(output)) < len(canonical(original)) / 2
        captured["ref"] = output["documentRef"]
        return "completion_evidence_read", {"documentId": captured["ref"]}

    pages = []

    def follow_original(body):
        page = last_output(body)
        pages.append(page)
        assert page["displayOnly"] is True and "completeDelivered" not in page
        if page["nextCursor"]:
            return "completion_evidence_read", {"cursor": page["nextCursor"]}
        return "completion_coverage_audit_submit", {"status": "inconclusive", "requirements": [],
            "findings": ["Challenge source delivery does not independently establish semantic adequacy."]}

    _native(monkeypatch, case, [("review_test", {"criterionIds": list(case["probe"].criterion_ids),
        "scenario": case["probe"].scenario, "code": case["probe"].code, "purpose": "acceptance"}),
        open_original, *([follow_original] * 30)])
    result = run_audit(case)
    assert result["status"] == "inconclusive"
    assert json.loads("".join(page["text"] for page in pages)) == original
