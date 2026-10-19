"""Display host completion feedback without replaying full execution traces.

The authenticated native history retains the original content as host metadata.
Only the displayed content changes; completion decisions and their receipts are
still owned and validated by the review ledger.
"""

from dataclasses import replace
import hashlib
import json

from minicode.completion_evidence_view import EvidenceViews
from minicode.tooling import ToolResult


class CompletionFeedbackViews:
    def __init__(self):
        self._views: dict[str, EvidenceViews] = {}
        self._contents: dict[str, str] = {}

    def _register_content(self, content: str):
        marker = ("\nCompletion evidence:\n" if "\nCompletion evidence:\n" in content
                  else "Current host completion decision (supersedes prior repair feedback):\n")
        prefix, found, body = content.rpartition(marker)
        if not found:
            return None
        try:
            receipt = json.loads(body)
        except (ValueError, TypeError):
            return None
        if not isinstance(receipt, dict) or receipt.get("completionVersion") != 1:
            return None
        digest = receipt.get("patchDigest")
        if not isinstance(digest, str):
            return None
        views = self._views.setdefault(digest, EvidenceViews(candidate_digest=digest))
        display = views.view(receipt, compact=True)
        content_digest = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        self._contents[content_digest] = content
        return prefix + found, receipt, display, digest

    def project_message(self, message: dict) -> dict:
        if not any(message.get(key) for key in (
                "_implementation_completion_feedback", "_superseded_completion_feedback",
                "_implementation_completion_status")):
            return message
        content = message.get("_completion_evidence_original", message.get("content"))
        if not isinstance(content, str):
            return message
        content_digest = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        previous = message.get("_completion_evidence_projection")
        if previous is not None and (not isinstance(previous, dict)
                or previous.get("originalContentDigest") != content_digest):
            raise ValueError("retained completion feedback original changed")
        history = message.get("_completion_evidence_history", [])
        if not isinstance(history, list) or any(not isinstance(value, str) for value in history):
            raise ValueError("retained completion evidence history changed")
        for original in history:
            self._register_content(original)
        registered = self._register_content(content)
        if registered is None:
            return message
        prefix, receipt, display, digest = registered
        # A new decision replaces the active feedback, but must not orphan
        # documents previously read by this same owner after process restart.
        retained = {**message, "_completion_evidence_history": [value
                    for key, value in self._contents.items() if key != content_digest]}
        if display == receipt:
            return retained
        if message.get("_implementation_completion_status"):
            # An approval record is not a repair packet. Keep its exact decision
            # addressable without replaying every historical witness mapping.
            display = {key: display[key] for key in (
                "completionVersion", "executorId", "attempt", "status", "patchDigest",
                "continuesSameExecutor", "displayOnly", "documentRef", "originalDigest",
                "candidateDigest") if key in display}
            display["recordNotice"] = "This records one host decision. Later host decisions supersede it; original evidence is available by documentRef."
        return {**retained, "content": prefix + json.dumps(display, ensure_ascii=False, sort_keys=True),
                "_completion_evidence_original": content,
                "_completion_evidence_projection": {
                    "displayOnly": True, "originalContentDigest": content_digest,
                    "documentRef": display["documentRef"], "candidateDigest": digest}}

    def tool_definition(self):
        # The schema is independent of candidate identity. Each original keeps
        # its own candidate-bound document store across later repair attempts.
        template = EvidenceViews(candidate_digest="unbound").tool_definition()

        def read(value, _context):
            candidate = value.get("candidateDigest") if isinstance(value, dict) else None
            matches = []
            for digest, views in self._views.items():
                if candidate is not None and candidate != digest:
                    continue
                try:
                    result = views.read(value)
                except (ValueError, TypeError, KeyError):
                    continue
                matches.append(result)
            if len(matches) == 1:
                return ToolResult(ok=True, output=json.dumps(matches[0], ensure_ascii=False, sort_keys=True))
            if len(matches) > 1:
                return ToolResult(ok=False, output=json.dumps({"errorCode": "completion_evidence_candidate_required",
                    "message": "This original appears in multiple candidate snapshots. Supply candidateDigest from its display reference.",
                    "candidateDigests": [row["candidateDigest"] for row in matches]}))
            return ToolResult(ok=False, output=json.dumps({"errorCode": "completion_evidence_invalid",
                "message": "Unknown evidence document or expired cursor. Reopen the documentRef from retained completion feedback with documentId; cursors belong to this process."}))

        return replace(template, run=read)
