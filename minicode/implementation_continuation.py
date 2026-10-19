"""Host-owned restoration of an interrupted implementation session.

This capability preserves identity and context; it grants no permission and no
completion approval. Candidate checks and requirement review run again after
restoration. Neither a tool result nor model text can create this capability.
"""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import re

from minicode.implementation_execution import ImplementationSpec, implementation_candidate_digest


def continuation_digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def implementation_spec_digest(spec):
    if not isinstance(spec, ImplementationSpec):
        raise ValueError("continuation requires a host-bound implementation specification")
    return continuation_digest(asdict(spec))


class HostImplementationContinuation:
    """Validated host checkpoint, bound to original work and actual messages."""

    _FIELDS = frozenset({"continuationVersion", "executorId", "attempt", "candidateDigest",
                         "specDigest", "messagesDigest", "reviewState", "completionFeedback"})

    def __init__(self, document, *, spec, messages):
        if not isinstance(document, dict):
            raise ValueError("invalid implementation continuation fields")
        version = document.get("continuationVersion")
        fields = self._FIELDS | {"migrationState"} if version == 2 else self._FIELDS
        if set(document) != fields:
            raise ValueError("invalid implementation continuation fields")
        if type(document["continuationVersion"]) is not int or document["continuationVersion"] not in {1, 2}:
            raise ValueError("unsupported implementation continuation")
        if not isinstance(document["executorId"], str) or not re.fullmatch(
                r"implementation_[0-9a-f]{32}", document["executorId"]):
            raise ValueError("invalid original implementation owner")
        if type(document["attempt"]) is not int or document["attempt"] < 0:
            raise ValueError("invalid original completion attempt")
        if not isinstance(document["candidateDigest"], str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", document["candidateDigest"]):
            raise ValueError("continuation requires the interrupted candidate identity")
        if document["specDigest"] != implementation_spec_digest(spec):
            raise ValueError("continuation specification changed")
        if (not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages)
                or document["messagesDigest"] != continuation_digest(messages)):
            raise ValueError("continuation messages changed")
        state = document["reviewState"]
        if state is not None and (not isinstance(state, dict) or state.get("objective") != spec.objective):
            raise ValueError("continuation review objective changed")
        migration_state = document.get("migrationState")
        if migration_state is not None:
            if state is None:
                raise ValueError("migration continuation requires its original review ledger")
            from minicode.completion_probe_migration import validate_continuation_state
            validate_continuation_state(spec, state, migration_state,
                candidate_digest=document["candidateDigest"])
        feedback = document["completionFeedback"]
        if feedback is not None and (
                not isinstance(feedback, dict) or feedback.get("role") != "user"
                or feedback.get("_implementation_completion_feedback") is not True
                or not isinstance(feedback.get("content"), str)
                or feedback not in messages):
            raise ValueError("continuation feedback must be retained actual parent context")
        # Defensive copying prevents a later mutation of the input document from
        # changing the identity, evidence or feedback after it was validated.
        self._document = deepcopy(document)
        self._revalidation_request = None
        self._coverage_recovery = None

    @classmethod
    def from_document(cls, document, *, spec, messages):
        return cls(document, spec=spec, messages=messages)

    @classmethod
    def for_revalidation(cls, document, *, spec, messages, request):
        """Reopen a host-authenticated approval without rewriting its history.

        The calling host authenticates the archive and terminal execution. This
        constructor binds that explicit request to the exact native checkpoint;
        neither a model tool nor a failed external judgment creates authority.
        """
        result = cls(document, spec=spec, messages=messages)
        state = document.get("reviewState")
        reviews = state.get("reviews") if isinstance(state, dict) else None
        approval = reviews[-1] if isinstance(reviews, list) and reviews else None
        if (not isinstance(approval, dict) or approval.get("status") != "approved"
                or approval.get("patchDigest") != document["candidateDigest"]
                or not isinstance(approval.get("evidenceRef"), str)
                or not approval["evidenceRef"]):
            raise ValueError("revalidation requires the latest native candidate approval")
        expected = {"revalidationVersion": 1, "reason": "public_requirement_revalidation",
            "checkpointDigest": continuation_digest(document),
            **{key: document[key] for key in ("executorId", "attempt", "candidateDigest",
                                             "specDigest", "messagesDigest")},
            "approvalEvidenceRef": approval["evidenceRef"],
            "approvalDigest": continuation_digest(approval)}
        if (not isinstance(request, dict) or type(request.get("revalidationVersion")) is not int
                or type(request.get("attempt")) is not int or request != expected):
            raise ValueError("revalidation request does not bind the original approved checkpoint")
        result._revalidation_request = deepcopy(request)
        return result

    @classmethod
    def for_coverage_recovery(cls, document, *, spec, messages, recovery):
        from minicode.coverage_audit_recovery import HostCoverageAuditRecovery
        if not isinstance(recovery, HostCoverageAuditRecovery):
            raise TypeError("coverage recovery requires a typed host derivation")
        result = cls(document, spec=spec, messages=messages)
        recovery.validate_for(result._document, spec=spec)
        result._coverage_recovery = recovery
        return result

    @property
    def coverage_recovery_receipt(self):
        return self._coverage_recovery.receipt_record if self._coverage_recovery is not None else None

    @property
    def revalidation_request(self):
        return deepcopy(self._revalidation_request)

    @property
    def requires_revalidation(self):
        return self._revalidation_request is not None

    @classmethod
    def capture(cls, *, spec, context, messages, executor_id, attempt, review_state=None, migration_state=None):
        feedback = next((m for m in reversed(messages)
                         if m.get("_implementation_completion_feedback")), None)
        document = {"continuationVersion": 1, "executorId": executor_id, "attempt": attempt,
                    "candidateDigest": implementation_candidate_digest(spec, context),
                    "specDigest": implementation_spec_digest(spec),
                    "messagesDigest": continuation_digest(messages), "reviewState": review_state,
                    "completionFeedback": feedback}
        if migration_state is not None:
            document.update(continuationVersion=2, migrationState=migration_state)
        return cls(document, spec=spec, messages=messages)

    @property
    def document(self):
        return deepcopy(self._document)

    @property
    def executor_id(self):
        return self._document["executorId"]

    @property
    def attempt(self):
        return self._document["attempt"]

    @property
    def feedback(self):
        return deepcopy(self._document["completionFeedback"])

    @property
    def migration_state(self):
        return deepcopy(self._document.get("migrationState"))

    def validate(self, *, spec, messages, context):
        # Revalidate at the actual invocation, before any model call. The host's
        # permissions still govern every file read used to compute this digest.
        type(self)(self._document, spec=spec, messages=messages)
        if self.requires_revalidation:
            type(self).for_revalidation(self._document, spec=spec, messages=messages,
                                       request=self._revalidation_request)
        if self._coverage_recovery is not None:
            self._coverage_recovery.validate_for(self._document, spec=spec)
        self.validate_candidate(spec=spec, context=context)

    def validate_candidate(self, *, spec, context):
        if self._document["specDigest"] != implementation_spec_digest(spec):
            raise ValueError("continuation specification changed")
        if implementation_candidate_digest(spec, context) != self._document["candidateDigest"]:
            raise ValueError("continuation candidate changed")

    def restore_review(self, spec):
        from minicode.implementation_completion_review import ImplementationCompletionReview

        state = self._document["reviewState"]
        if self._coverage_recovery is not None:
            self._coverage_recovery.validate_for(self._document, spec=spec)
            state = {**state, "pendingCoverageAudit": self._coverage_recovery.pending_state}
        return (ImplementationCompletionReview.from_state(spec, state) if state is not None
                else ImplementationCompletionReview(spec))
