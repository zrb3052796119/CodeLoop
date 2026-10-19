"""Small model-facing views of immutable host evidence.

These are display documents, never replacement verification receipts or audit
delivery authority. Full originals remain addressable; filtering only selects
observations already present in them, and never infers behavioral coverage.
"""

from __future__ import annotations

import json
from threading import RLock
from typing import Any

from minicode.pytest_execution_evidence import validate_execution_report
from minicode.review_evidence_documents import EvidenceDocuments, canonical


class EvidenceViews:
    # Diagnostics are excerpts, never newly interpreted test conclusions. The
    # original string is independently addressable and can be read in pages.
    _STREAM_EXCERPT_CHARS = 1500

    def __init__(self, *, candidate_digest: str, page_size: int = 8000):
        self.documents = EvidenceDocuments(candidate_digest=candidate_digest, page_size=page_size)
        self._values: dict[str, Any] = {}
        self._lock = RLock()

    def _register(self, value: Any, kind: str) -> dict[str, Any]:
        document_id = self.documents.add(value, kind, factor=False)
        if document_id not in self._values:
            self._values[document_id] = json.loads(canonical(value))
        descriptor = self.documents.descriptor(document_id)
        return {"displayOnly": True, "documentRef": document_id,
                "candidateDigest": self.documents.candidate_digest,
                "originalDigest": descriptor["originalDigest"]}

    def _execution_view(self, value: Any, report: dict | None = None) -> dict:
        reference = self._register(value, "completion_execution_report")
        cases = value.get("cases", []) if isinstance(value, dict) else []
        cases = cases if isinstance(cases, list) else []
        valid = validate_execution_report(report) if report is not None else None
        return {**reference, "validation": "valid" if valid else "invalid" if valid is False else "unbound",
                "complete": bool(cases) and all(isinstance(case, dict) and case.get("complete") is True for case in cases),
                "caseCount": len(cases),
                "assertionCount": sum(len(case.get("assertions", [])) for case in cases if isinstance(case, dict)),
                "callCount": sum(len(case.get("calls", [])) for case in cases if isinstance(case, dict)),
                "readHint": "completion_evidence_read: filters={} for case index; select caseNodeId, assertionId or consumer for observations; omit filters for the full original."}

    def _receipt_index(self, wrapper: Any, *, include_diagnostics: bool = True) -> Any:
        if not isinstance(wrapper, dict):
            return self._walk(wrapper, compact=True)
        receipt = wrapper.get("receipt", wrapper)
        if not isinstance(receipt, dict):
            return self._walk(wrapper, compact=True)
        result = {**self._register(wrapper, "completion_receipt"),
                  **{key: wrapper[key] for key in ("probeId", "candidateDigest", "receiptRef", "sourceRef") if key in wrapper},
                  **{key: receipt[key] for key in ("status", "patchDigest", "reason", "sourceDigest") if key in receipt}}
        if result.get("candidateDigest") != self.documents.candidate_digest:
            result["originalCandidateDigest"] = result["candidateDigest"]
            result["candidateDigest"] = self.documents.candidate_digest
        evidence = receipt.get("evidence")
        if isinstance(evidence, dict) and "exitCode" in evidence:
            result["exitCode"] = evidence["exitCode"]
        report = receipt.get("testReport")
        if isinstance(report, dict):
            result["tests"] = {key: report[key] for key in ("tests", "passed", "failed", "failures", "errors", "skipped", "collected", "status") if key in report}
            case_report = report.get("caseReport", {})
            cases = case_report.get("cases", []) if isinstance(case_report, dict) else []
            result["failureCases"] = [self._walk(case, compact=False) for case in cases
                                      if isinstance(case, dict) and case.get("outcome") != "passed"]
            if "executionReport" in report:
                result["executionReport"] = self._execution_view(report["executionReport"], report)
        if include_diagnostics and receipt.get("status") != "passed":
            for key in ("evidence", "stdout", "stderr", "failureReason", "error", "message"):
                if key in receipt:
                    result[key] = (self._stream_view(receipt[key]) if key in {"stdout", "stderr"}
                                   else self._failure_view(receipt[key]))
        return result

    def _stream_view(self, value: Any) -> Any:
        if isinstance(value, str) and len(value) > self._STREAM_EXCERPT_CHARS:
            head = (self._STREAM_EXCERPT_CHARS + 1) // 2
            tail = self._STREAM_EXCERPT_CHARS - head
            return {**self._register(value, "completion_stream_preview"),
                    "totalChars": len(value),
                    "excerpt": [{"startOffset": 0, "endOffset": head, "text": value[:head]},
                                {"startOffset": len(value) - tail, "endOffset": len(value), "text": value[-tail:]}],
                    "readHint": "Excerpt only. Read documentRef with completion_evidence_read for the exact full text."}
        if isinstance(value, dict):
            result = {key: self._stream_view(child) if key == "preview"
                      else self._walk(child, compact=False) for key, child in value.items()
                      if key != "previewBase64"}
            if result != value:
                result.update(self._register(value, "completion_stream"))
            return result
        return self._walk(value, compact=False)

    def _failure_view(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._stream_view(child) if key in {"stdout", "stderr"}
                    else self._failure_view(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self._failure_view(child) for child in value]
        return self._walk(value, compact=False)

    def _diagnostic_index(self, value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("source"), dict):
            return self._walk(value, compact=True)
        source = value["source"]
        result = {**self._register(value, "completion_diagnostic_observation"),
                  "source": {**self._register(source, "completion_diagnostic_source"),
                             **{key: self._walk(source[key], compact=False)
                                for key in ("probeId", "criterionIds", "scenario", "purpose", "sourceDigest") if key in source}},
                  **{key: self._walk(child, compact=True) for key, child in value.items()
                     if key not in {"source", "receipts"}}}
        if isinstance(value.get("receipts"), list):
            # Diagnostic history cannot establish current acceptance coverage or
            # a repair obligation. Keep every outcome and its immutable source;
            # reopening a record retrieves its complete original diagnostics.
            result["receipts"] = [self._receipt_index(receipt, include_diagnostics=False)
                                  for receipt in value["receipts"]]
        elif "receipts" in value:
            result["receipts"] = self._walk(value["receipts"], compact=True)
        return result

    def _walk(self, value: Any, *, compact: bool) -> Any:
        if isinstance(value, dict):
            if value.get("displayOnly") is True and value.get("documentRef") in self._values:
                return json.loads(canonical(value))
            result = {}
            for key, child in value.items():
                if key == "executionReport":
                    result[key] = self._execution_view(child, value)
                elif compact and key == "probeReceipts" and isinstance(child, list):
                    result[key] = [self._receipt_index(item) for item in child]
                elif compact and key == "diagnosticObservations" and isinstance(child, list):
                    result[key] = [self._diagnostic_index(item) for item in child]
                else:
                    result[key] = self._walk(child, compact=compact)
            return result
        if isinstance(value, list):
            return [self._walk(child, compact=compact) for child in value]
        return value

    def view(self, value: Any, *, compact: bool = False) -> Any:
        """Do not mutate originals, replace sources, or turn summaries into authority."""
        with self._lock:
            if isinstance(value, dict) and value.get("displayOnly") is True and value.get("documentRef") in self._values:
                return json.loads(canonical(value))
            result = self._walk(value, compact=compact)
            if result == value:
                return result
            reference = self._register(value, "completion_evidence")
            if (isinstance(result, dict) and "candidateDigest" in value
                    and value["candidateDigest"] != self.documents.candidate_digest):
                result["originalCandidateDigest"] = value["candidateDigest"]
            return {**result, **reference} if isinstance(result, dict) else {**reference, "items": result}

    project = view

    def reference(self, value: Any, *, kind: str = "completion_reference") -> dict[str, Any]:
        """Register an exact read-only original without expanding model context."""
        with self._lock:
            return self._register(value, kind)

    @staticmethod
    def _execution(value: Any) -> dict:
        if isinstance(value, dict):
            if isinstance(value.get("cases"), list) and "junitDigest" in value:
                return value
            for key in ("executionReport", "testReport", "receipt"):
                if key in value:
                    try:
                        return EvidenceViews._execution(value[key])
                    except ValueError:
                        pass
        raise ValueError("filters require an executionReport or a receipt containing one")

    def _filtered(self, document_id: str, filters: Any) -> str:
        if (not isinstance(filters, dict) or set(filters) - {"caseNodeId", "assertionId", "consumer"}
                or any(not isinstance(filters[key], str) or not filters[key] for key in ("caseNodeId", "assertionId") if key in filters)):
            raise ValueError("filters accept nonempty caseNodeId/assertionId strings and a consumer object")
        consumer = filters.get("consumer")
        if "consumer" in filters and (not isinstance(consumer, dict) or not consumer
                or set(consumer) - {"path", "qualname"}
                or any(not isinstance(value, str) or not value for value in consumer.values())):
            raise ValueError("consumer accepts a nonempty path and/or qualname; matching is exact")
        original = self._values[document_id]
        execution = self._execution(original)
        selected = []
        for case in execution["cases"]:
            if "caseNodeId" in filters and case["nodeId"] != filters["caseNodeId"]:
                continue
            assertions = case["assertions"]
            if "assertionId" in filters:
                assertions = [item for item in assertions if item["assertionId"] == filters["assertionId"]]
                if not assertions:
                    continue
            if not filters:
                selected.append({key: case[key] for key in ("nodeId", "complete", "limitations")} | {
                    "assertionCount": len(assertions), "callCount": len(case["calls"])})
                continue
            calls = case["calls"]
            matched = []
            if consumer is not None:
                matched = [item["callId"] for item in calls if all(item.get(key) == value for key, value in consumer.items())]
                if not matched:
                    continue
                by_id = {item["callId"]: item for item in calls}
                retained = set(matched)
                for call_id in matched:
                    visited = set()
                    while by_id[call_id]["parentCallId"] is not None:
                        parent = by_id[call_id]["parentCallId"]
                        if parent not in by_id or parent in visited:
                            raise ValueError("original execution report has a broken consumer ancestor chain")
                        visited.add(parent)
                        retained.add(parent)
                        call_id = parent
                calls = [item for item in calls if item["callId"] in retained]
            elif "assertionId" in filters:
                calls = []
            selected.append({**case, "assertions": assertions, "calls": calls, "matchedCallIds": matched})
        result = {"displayOnly": True, "originalDocumentRef": document_id,
                  "originalDigest": self.documents.descriptor(document_id)["originalDigest"],
                  "candidateDigest": self.documents.candidate_digest,
                  "filters": filters, "cases": selected,
                  "interpretation": "Observed in the same case; does not infer that a call caused an assertion or proves requirement coverage."}
        return self._register(result, "completion_execution_selection")["documentRef"]

    def _read(self, value: Any) -> dict:
        if not isinstance(value, dict):
            raise ValueError("open with documentId and optional filters, or continue with cursor; candidateDigest optionally selects its evidence store")
        request = dict(value)
        if "candidateDigest" in request:
            if request.pop("candidateDigest") != self.documents.candidate_digest:
                raise ValueError("candidateDigest does not match this evidence store; use the candidateDigest attached to documentRef")
        if set(request) not in ({"documentId"}, {"documentId", "filters"}, {"cursor"}):
            raise ValueError("open with documentId and optional filters, or continue with cursor; candidateDigest optionally selects its evidence store")
        if "documentId" in request:
            document_id = request["documentId"]
            if not isinstance(document_id, str) or document_id not in self._values:
                raise ValueError("unknown evidence document; rebuild the view from current host evidence before reopening")
            if "filters" in request:
                request = {"documentId": self._filtered(document_id, request["filters"])}
        # This store provides views, not evidence-delivery credit for approval.
        page = self.documents.read(request, deliver=False)
        page.pop("completeDelivered", None)
        return {**page, "displayOnly": True}

    def read(self, value: Any) -> dict:
        with self._lock:
            return self._read(value)

    def tool_definition(self):
        from minicode.capability_contracts import CapabilityDomain, CapabilityEffects, CapabilityProfile, CapabilityScope
        from minicode.tooling import ToolDefinition, ToolResult

        def run(value, _context):
            try:
                return ToolResult(ok=True, output=canonical(self.read(value)))
            except (ValueError, TypeError, KeyError) as error:
                return ToolResult(ok=False, output=canonical({"errorCode": "completion_evidence_invalid", "message": str(error)}))

        return ToolDefinition(name="completion_evidence_read",
            description="Read the full immutable host evidence behind a displayOnly documentRef. Open with documentId and its candidateDigest to select the evidence store; follow nextCursor to null. candidateDigest is optional for an unambiguous single store. Optional filters={} returns a compact case index; caseNodeId/assertionId or exact consumer path/qualname selects observations and consumer ancestors. Views do not replace original verification authority.",
            input_schema={"type": "object", "additionalProperties": False,
                "properties": {"documentId": {"type": "string"}, "cursor": {"type": "string"},
                    "candidateDigest": {"type": "string"},
                    "filters": {"type": "object", "additionalProperties": False, "properties": {
                        "caseNodeId": {"type": "string"}, "assertionId": {"type": "string"},
                        "consumer": {"type": "object", "additionalProperties": False,
                            "properties": {"path": {"type": "string"}, "qualname": {"type": "string"}}, "minProperties": 1}}}},
                "oneOf": [{"required": ["documentId"], "not": {"required": ["cursor"]}},
                          {"required": ["cursor"], "not": {"anyOf": [{"required": ["documentId"]}, {"required": ["filters"]}]}}]},
            validator=lambda value: value, run=run,
            capability_profile=CapabilityProfile(domain=CapabilityDomain.MEMORY, scope=CapabilityScope.READONLY,
                effects=CapabilityEffects(read_only=True, destructive=False, external=False, executes_code=False, delegates=False),
                purposes=("Read immutable host completion evidence without changing files or granting approval",)))
