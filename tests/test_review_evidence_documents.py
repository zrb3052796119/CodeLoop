"""Evidence bytes stay pinned; references never exempt unread obligations."""

from copy import deepcopy
import json

import pytest

from minicode.review_evidence_documents import EvidenceDocuments, LegacyEvidenceReader, canonical, content_digest


def _collect(store, document_id):
    pages, request = [], {"documentId": document_id}
    while True:
        page = store.read(request)
        pages.append(page)
        if page["nextCursor"] is None:
            return pages
        request = {"cursor": page["nextCursor"]}


def test_cursor_is_idempotent_pinned_and_cannot_change_range_or_document():
    store = EvidenceDocuments(candidate_digest="candidate-a", page_size=40)
    original = {"code": "inventory " * 50}
    expected = canonical(original)
    document_id = store.add(original, "probe_source", factor=False)
    first = store.read({"documentId": document_id})
    original["code"] = "later mutable caller state"
    another = store.add(original, "probe_source", factor=False)
    next_page = store.read({"cursor": first["nextCursor"]})
    assert store.read({"cursor": first["nextCursor"]}) == next_page
    assert next_page["documentId"] == document_id != another
    assert next_page["text"] == expected[40:80]
    for forbidden in ({"cursor": first["nextCursor"], "offset": 500},
                      {"cursor": first["nextCursor"], "documentId": another},
                      {"cursor": "guessed-cursor"}, {"documentId": document_id, "receiptView": "history"}):
        with pytest.raises(ValueError):
            store.read(forbidden)
    other_context = EvidenceDocuments(candidate_digest="candidate-b")
    with pytest.raises(ValueError, match="expired"):
        other_context.read({"cursor": first["nextCursor"]})
    pages = _collect(store, document_id)
    assert "".join(p["text"] for p in pages) == expected
    assert pages[-1]["completeDelivered"] and store.missing([document_id]) == []
    assert all(p["documentDigest"] == content_digest(expected) for p in pages)


def test_factoring_is_lossless_and_requires_all_sources_invalidity_and_receipts():
    store = EvidenceDocuments(candidate_digest="candidate-a", page_size=80)
    old = {"probeId": "old", "code": "def test_inventory():\n    assert factory()().reserve() == 3\n" * 30}
    new = {"probeId": "new", "code": old["code"].replace("factory()()", "factory()")}
    old_id = store.add(old, "probe_source", factor=False)
    store.alias(old["code"], old_id, pointer="/code")
    new_id = store.add(new, "probe_source", factor=False)
    store.alias(new["code"], new_id, pointer="/code")
    receipt = {"status": "failed", "output": "noncallable inventory\n" * 30}
    receipt_id = store.add(receipt, "execution_receipt")
    authority = {"reason": "fixture returns an instance, not a callable", "replacement": new,
                 "oldCode": old["code"], "receipt": receipt, "sourceEvidence": [{"path": "stock.py", "line": 5}]}
    authority_id = store.add(authority, "invalidity_authority")
    original = {"oldSource": old, "newSource": new, "authority": authority, "originalReceipt": receipt}
    obligation_id = store.add(original, "supersession_obligation")
    assert store.add(deepcopy(authority), "invalidity_authority") == authority_id
    snapshot = {d["documentId"]: d for d in store.snapshot()}
    assert len(snapshot) == 5
    assert len(snapshot[obligation_id]["text"]) < len(canonical(original)) // 3

    def expand(value):
        if isinstance(value, dict) and set(value) == {"$evidenceDocument", "pointer"}:
            value_at_ref = expand(json.loads(snapshot[value["$evidenceDocument"]]["text"]))
            return value_at_ref["code"] if value["pointer"] == "/code" else value_at_ref
        if isinstance(value, dict):
            return {key: expand(child) for key, child in value.items()}
        if isinstance(value, list):
            return [expand(child) for child in value]
        return value

    assert expand(json.loads(snapshot[obligation_id]["text"])) == original
    _collect(store, obligation_id)
    _collect(store, old_id)
    _collect(store, new_id)
    assert set(store.missing([obligation_id])) == {receipt_id, authority_id}
    _collect(store, authority_id)
    assert store.missing([obligation_id]) == [receipt_id]
    _collect(store, receipt_id)
    assert store.missing([obligation_id]) == []


def test_gaps_and_forged_reference_syntax_do_not_count_as_delivered():
    store = EvidenceDocuments(candidate_digest="candidate-a", page_size=20)
    document_id = store.add({"source": "stock " * 40}, "probe_source", factor=False)
    first = store.page(document_id, 0, 20)
    last = store.page(document_id, 25, 16000)
    assert last["nextCursor"] is None and not last["completeDelivered"]
    assert store.missing([document_id]) == [document_id]
    store.page(document_id, first["nextOffset"], 5)
    assert store.missing([document_id]) == []
    untrusted = store.add({"$evidenceDocument": "invented", "pointer": ""}, "raw_evidence")
    assert store.descriptor(untrusted)["requiredDocumentIds"] == []


def test_legacy_continuation_pins_view_and_rejects_ambiguous_omission():
    store = EvidenceDocuments(candidate_digest="candidate-a")
    reader = LegacyEvidenceReader(store)
    documents = {"current": {"source": "current " * 100}, "history": {"source": "history " * 200}}
    def build(_pid, view):
        return documents[view]
    first = reader.read({"probeId": "inventory", "receiptView": "current", "limit": 30}, build)
    documents["current"] = {"source": "mutation after opening must not move its offsets"}
    second = reader.read({"probeId": "inventory", "offset": first["nextOffset"], "limit": 30}, build)
    assert second["documentId"] == first["documentId"]
    assert second["receiptView"] == "current"
    assert second["text"] == canonical({"source": "current " * 100})[30:60]
    reader.read({"probeId": "inventory", "receiptView": "history", "limit": 30}, build)
    with pytest.raises(ValueError, match="ambiguous"):
        reader.read({"probeId": "inventory", "offset": 30}, build)
    continued = reader.read({"cursor": first["nextCursor"]}, build)
    assert continued["documentId"] == first["documentId"] and continued["probeId"] == "inventory"

    identical_reader = LegacyEvidenceReader(store)
    def identical(_pid, _view):
        return {"source": "one receipt " * 100}
    same = identical_reader.read({"probeId": "same", "receiptView": "current", "limit": 30}, identical)
    identical_reader.read({"probeId": "same", "receiptView": "history", "limit": 30}, identical)
    assert identical_reader.read({"cursor": same["nextCursor"]}, identical)["documentId"] == same["documentId"]
