"""Immutable, losslessly factored evidence with host-owned continuations.

Delivery proves that bytes reached an audit context, never their semantics.
References replace repeated values, not evidence: every referenced document is
part of the delivery obligation, including a reference to only one of its fields.
"""

from __future__ import annotations

import hashlib
import json
import secrets


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def content_digest(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


class EvidenceDocuments:
    def __init__(self, *, candidate_digest, page_size=8000):
        self.candidate_digest = candidate_digest
        self.page_size = page_size
        self._documents = {}
        self._aliases = {}
        self._cursors = {}
        self._next = {}
        self._delivered = {}

    @classmethod
    def from_snapshot(cls, *, candidate_digest, documents):
        store = cls(candidate_digest=candidate_digest)
        if not isinstance(documents, list):
            raise ValueError("evidence snapshot must contain documents")
        for value in documents:
            if not isinstance(value, dict) or set(value) != {"documentId", "documentDigest", "documentType",
                    "originalDigest", "requiredDocumentIds", "text"}:
                raise ValueError("invalid immutable evidence document")
            document = json.loads(canonical(value))
            digest = content_digest(document["text"])
            if (document["documentDigest"] != digest or document["documentId"] != "evidence_" + digest[7:]
                    or document["documentId"] in store._documents):
                raise ValueError("evidence document content or identity changed")
            store._documents[document["documentId"]] = document
        expanded = {}

        def expand(document_id, trail=()):
            if document_id in trail or document_id not in store._documents:
                raise ValueError("evidence reference cycle or unknown document")
            if document_id in expanded:
                return expanded[document_id]
            document = store._documents[document_id]
            dependencies = set()

            def resolve(value):
                if isinstance(value, dict):
                    if set(value) == {"$evidenceDocument", "pointer"} and value["$evidenceDocument"] in document["requiredDocumentIds"]:
                        dependency = value["$evidenceDocument"]
                        dependencies.add(dependency)
                        result = expand(dependency, (*trail, document_id))
                        pointer = value["pointer"]
                        if pointer not in {"", "/code"}:
                            raise ValueError("unknown evidence reference pointer")
                        return result["code"] if pointer else result
                    return {key: resolve(child) for key, child in value.items()}
                if isinstance(value, list):
                    return [resolve(child) for child in value]
                return value

            result = resolve(json.loads(document["text"]))
            if (sorted(dependencies) != document["requiredDocumentIds"]
                    or content_digest(canonical(result)) != document["originalDigest"]):
                raise ValueError("evidence dependency closure or original binding changed")
            expanded[document_id] = result
            return result

        for document_id, document in store._documents.items():
            value = expand(document_id)
            store.alias(value, document_id)
            if document["documentType"] == "probe_source":
                store.alias(value["code"], document_id, pointer="/code")
        return store

    def restore_delivery(self, request, page):
        """Replay only an authenticated result actually delivered in messages."""
        if not isinstance(request, dict) or set(request) not in ({"documentId"}, {"cursor"}):
            raise ValueError("invalid delivered evidence request")
        if not isinstance(page, dict) or page.get("documentId") not in self._documents:
            raise ValueError("delivered evidence document is unknown")
        document_id = page["documentId"]
        document = self._documents[document_id]
        offset = page.get("offset")
        if type(offset) is not int or offset < 0 or not isinstance(page.get("text"), str):
            raise ValueError("invalid delivered evidence range")
        end = offset + len(page["text"])
        if (end > len(document["text"]) or end <= offset
                or page["text"] != document["text"][offset:end]
                or page.get("totalChars") != len(document["text"])
                or page.get("candidateDigest") != self.candidate_digest
                or any(page.get(key) != value for key, value in self.descriptor(document_id).items())):
            raise ValueError("delivered evidence bytes or binding changed")
        if "documentId" in request:
            if request["documentId"] != document_id or offset != 0:
                raise ValueError("delivered evidence opening changed")
        elif self._cursors.get(request.get("cursor")) != (document_id, offset):
            raise ValueError("delivered evidence cursor has no preceding host issue")
        cursor = page.get("nextCursor")
        if end < len(document["text"]):
            if (not isinstance(cursor, str) or not cursor.startswith("cursor_") or page.get("nextOffset") != end
                    or (cursor in self._cursors and self._cursors[cursor] != (document_id, end))
                    or ((document_id, end) in self._next and self._next[(document_id, end)] != cursor)):
                raise ValueError("delivered evidence continuation changed")
            self._cursors[cursor] = (document_id, end)
            self._next[(document_id, end)] = cursor
        elif cursor is not None or page.get("nextOffset") is not None:
            raise ValueError("completed evidence document has a continuation")
        self._delivered.setdefault(document_id, set()).add((offset, end))

    def add(self, value, kind, *, factor=True):
        original = canonical(value)
        if original in self._aliases and not self._aliases[original]["pointer"]:
            return self._aliases[original]["$evidenceDocument"]
        dependencies = set()

        def project(item):
            reference = self._aliases.get(canonical(item))
            if reference is not None:
                dependencies.add(reference["$evidenceDocument"])
                return dict(reference)
            if isinstance(item, dict):
                return {key: project(child) for key, child in item.items()}
            if isinstance(item, list):
                return [project(child) for child in item]
            return item

        text = canonical(project(value) if factor else value)
        digest = content_digest(text)
        document_id = "evidence_" + digest.removeprefix("sha256:")
        document = {"documentId": document_id, "documentDigest": digest,
            "documentType": kind, "originalDigest": content_digest(original),
            "requiredDocumentIds": sorted(dependencies), "text": text}
        existing = self._documents.get(document_id)
        if existing is not None and any(existing[key] != document[key]
                for key in ("originalDigest", "requiredDocumentIds", "text")):
            raise ValueError("evidence document identity collision")
        self._documents.setdefault(document_id, document)
        self.alias(value, document_id)
        return document_id

    def alias(self, value, document_id, *, pointer=""):
        # Only host-created aliases can introduce dependencies. A string that
        # merely resembles a reference in untrusted evidence is never followed.
        if document_id not in self._documents:
            raise ValueError("unknown evidence document")
        self._aliases.setdefault(canonical(value), {"$evidenceDocument": document_id, "pointer": pointer})

    def descriptor(self, document_id):
        document = self._documents[document_id]
        return {key: list(value) if isinstance(value, list) else value
                for key, value in document.items() if key != "text"}

    def snapshot(self):
        return json.loads(canonical(list(self._documents.values())))

    def missing(self, document_ids):
        missing, visited = set(), set()

        def visit(document_id):
            if document_id in visited:
                return
            visited.add(document_id)
            document = self._documents[document_id]
            end = 0
            for first, last in sorted(self._delivered.get(document_id, ())):
                if first > end:
                    break
                end = max(end, last)
            if end != len(document["text"]):
                missing.add(document_id)
            for dependency in document["requiredDocumentIds"]:
                visit(dependency)

        for document_id in document_ids:
            visit(document_id)
        return sorted(missing)

    def page(self, document_id, offset=0, limit=None, *, deliver=True):
        if document_id not in self._documents:
            raise ValueError("unknown evidence document; use the current audit evidence index")
        limit = self.page_size if limit is None else limit
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 16000:
            raise ValueError("invalid evidence page")
        document = self._documents[document_id]
        if offset > len(document["text"]):
            raise ValueError("evidence offset is outside its immutable document")
        end = min(len(document["text"]), offset + limit)
        cursor = None
        if end < len(document["text"]):
            cursor = self._next.get((document_id, end))
            if cursor is None:
                cursor = "cursor_" + secrets.token_hex(24)
                self._next[(document_id, end)] = cursor
                self._cursors[cursor] = (document_id, end)
        previous = set(self._delivered.get(document_id, ()))
        self._delivered.setdefault(document_id, set()).add((offset, end))
        complete = document_id not in self.missing([document_id])
        if not deliver:
            self._delivered[document_id] = previous
        return {**self.descriptor(document_id), "candidateDigest": self.candidate_digest,
            "offset": offset, "text": document["text"][offset:end], "totalChars": len(document["text"]),
            "nextOffset": end if cursor else None, "nextCursor": cursor,
            "completeDelivered": complete}

    def read(self, value, *, deliver=True):
        if not isinstance(value, dict) or set(value) not in ({"documentId"}, {"cursor"}):
            raise ValueError("use exactly documentId to open evidence or cursor to continue; offsets and views are host-owned")
        if "cursor" in value:
            if not isinstance(value["cursor"], str) or value["cursor"] not in self._cursors:
                raise ValueError("unknown or expired evidence cursor; reopen its current documentId")
            document_id, offset = self._cursors[value["cursor"]]
            return self.page(document_id, offset, deliver=deliver)
        if not isinstance(value["documentId"], str):
            raise ValueError("documentId must name a current evidence document")
        return self.page(value["documentId"], deliver=deliver)


class LegacyEvidenceReader:
    """Keep offset callers working without silently switching their document."""

    def __init__(self, documents):
        self.documents = documents
        self._streams = {}

    def read(self, value, build):
        if not isinstance(value, dict) or set(value) - {"probeId", "offset", "limit", "receiptView", "cursor"}:
            raise ValueError("review_probe_read accepts probeId/offset/limit/receiptView or cursor")
        if "cursor" in value:
            page = self.documents.read(value)
            matches = [key for key, document_id in self._streams.items() if document_id == page["documentId"]]
            if not matches or len({key[0] for key in matches}) != 1:
                raise ValueError("cursor does not belong to this reader's opened probe document")
            # Current and history can be byte-identical (one receipt). The
            # content-addressed cursor is still unambiguous in that case.
            probe_id, view = matches[0]
            return {**page, "probeId": probe_id, "receiptView": view}
        offset = value.get("offset", 0)
        limit = value.get("limit", 8000)
        view = value.get("receiptView")
        probe_id = value.get("probeId")
        if view is not None and view not in {"current", "history"}:
            raise ValueError("receiptView must be history or current")
        if type(offset) is not int or offset < 0:
            raise ValueError("invalid evidence page")
        if view is None and offset:
            streams = [(key, did) for key, did in self._streams.items() if key[0] == probe_id]
            if len(streams) > 1:
                raise ValueError("ambiguous evidence continuation: provide the returned nextCursor or explicit receiptView")
            if streams:
                (probe_id, view), document_id = streams[0]
            else:
                view = "history"
        else:
            view = view or "history"
        key = (probe_id, view)
        if offset == 0 or key not in self._streams:
            document_id = self.documents.add(build(probe_id, view), "legacy_probe", factor=False)
            self._streams[key] = document_id
        else:
            document_id = self._streams[key]
        return {**self.documents.page(document_id, offset, limit), "receiptView": view, "probeId": probe_id}
