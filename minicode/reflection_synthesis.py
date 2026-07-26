"""Deterministic claim synthesis, validation, and durable-value selection.

The module consumes only ``TaskEvidence``.  It does not re-read execution
traces, call a model, persist memory, or reinterpret raw tool payloads.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, runtime_checkable

from minicode.reflection_evidence import (
    DecisionEvidence,
    ErrorEvidence,
    RecoveryEvidence,
    TaskEvidence,
    VerificationEvidence,
    sanitize_evidence_text,
)


ClaimType = Literal[
    "constraint",
    "dependency",
    "error_pattern",
    "root_cause",
    "recovery",
    "decision",
    "correction",
    "verification_rule",
    "warning",
]
ClaimSeverity = Literal["info", "warning", "error"]

CLAIM_MAX_TEXT_CHARS = 1_200
CLAIM_MAX_AUX_TEXT_CHARS = 600
CLAIM_MAX_ITEMS = 256


@runtime_checkable
class ReflectionSynthesizer(Protocol):
    """Common candidate-only synthesis contract."""

    def synthesize(
        self,
        task_description: str,
        evidence: TaskEvidence,
    ) -> ReflectionCandidate: ...

_GENERIC_CLAIMS = (
    "task completed successfully",
    "used unique tool",
    "used tools",
    "errors occurred with tool",
    "review error patterns",
    "consider breaking the task into smaller steps",
    "this approach worked",
    "follow best practices",
    "test after making changes",
)
_GENERIC_ERROR_MESSAGES = {
    "error",
    "failed",
    "failure",
    "operation failed",
    "operation failed before completion",
    "tool error",
    "unknown error",
    "an error occurred",
}
_READ_TOOLS = {"read_file"}
_SEARCH_TOOLS = {"grep_files", "search_files", "find_symbols", "find_references"}
_LIST_TOOLS = {"list_directory", "list_files", "directory_tree"}
_FORMAT_TOOLS = {"format_file", "formatter"}


def _ordered_unique(values: list[str] | tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))[:CLAIM_MAX_ITEMS]


def _normalize_text(value: Any) -> str:
    return " ".join(sanitize_evidence_text(value, CLAIM_MAX_TEXT_CHARS).strip().lower().split())


def _normalize_semantic_key(value: Any) -> str:
    return re.sub(
        r"[^a-z0-9_\u4e00-\u9fff]+",
        "_",
        sanitize_evidence_text(value, 160).lower(),
    ).strip("_") or "claim"


def _semantic_slug(value: str, prefix: str) -> str:
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", value.lower())
    body = "_".join(tokens[:12]).strip("_") or "fact"
    return f"{prefix}_{body}"[:160].rstrip("_")


def _text_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) >= 3
        and token
        not in {
            "after",
            "before",
            "changed",
            "current",
            "error",
            "failed",
            "passed",
            "project",
            "result",
            "test",
            "tests",
            "this",
            "tool",
            "using",
            "with",
        }
    }


def _cjk_bigrams(value: str) -> set[str]:
    chars = "".join(re.findall(r"[\u4e00-\u9fff]", value))
    return {chars[index : index + 2] for index in range(max(0, len(chars) - 1))}


def _event_position(evidence: TaskEvidence, event_ids: list[str] | tuple[str, ...]) -> int:
    positions = [evidence.event_positions[event_id] for event_id in event_ids if event_id in evidence.event_positions]
    return max(positions, default=-1)


@dataclass
class ReflectionClaim:
    """One reusable proposition linked to exact evidence records."""

    claim_id: str
    claim_type: ClaimType
    semantic_key: str
    statement: str
    evidence_ids: list[str]
    epistemic_status: Literal["confirmed", "inferred", "unknown"]
    applies_when: str = ""
    limitations: list[str] = field(default_factory=list)
    verification_ids: list[str] = field(default_factory=list)
    related_error_ids: list[str] = field(default_factory=list)
    related_recovery_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_type": self.claim_type,
            "semantic_key": self.semantic_key,
            "statement": self.statement,
            "evidence_ids": list(self.evidence_ids),
            "epistemic_status": self.epistemic_status,
            "applies_when": self.applies_when,
            "limitations": list(self.limitations),
            "verification_ids": list(self.verification_ids),
            "related_error_ids": list(self.related_error_ids),
            "related_recovery_ids": list(self.related_recovery_ids),
        }


@dataclass
class ReflectionCandidate:
    """Untrusted claim proposal produced before deterministic validation."""

    task_summary: str
    outcome: Literal["success", "failed", "unknown"]
    claims: list[ReflectionClaim] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    synthesis_diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_summary": self.task_summary,
            "outcome": self.outcome,
            "claims": [claim.to_dict() for claim in self.claims],
            "source_event_ids": list(self.source_event_ids),
            "synthesis_diagnostics": list(self.synthesis_diagnostics),
        }


@dataclass(frozen=True)
class ClaimValidationIssue:
    code: str
    message: str
    claim_id: str | None = None
    severity: ClaimSeverity = "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "claim_id": self.claim_id,
            "severity": self.severity,
        }


@dataclass
class ClaimValidationResult:
    valid_claims: list[ReflectionClaim] = field(default_factory=list)
    rejected_claims: list[ReflectionClaim] = field(default_factory=list)
    issues: list[ClaimValidationIssue] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return bool(self.valid_claims)

    def to_dict(self, *, include_rejected_text: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "accepted": self.accepted,
            "valid_claims": [claim.to_dict() for claim in self.valid_claims],
            "rejected_claim_ids": [claim.claim_id for claim in self.rejected_claims],
            "issues": [issue.to_dict() for issue in self.issues],
        }
        if include_rejected_text:
            result["rejected_claims"] = [claim.to_dict() for claim in self.rejected_claims]
        return result


@dataclass
class ReflectionValueDecision:
    accepted: bool = False
    reason_codes: list[str] = field(default_factory=lambda: ["missing_value_decision"])
    durable_signals: list[str] = field(default_factory=list)
    accepted_claim_ids: list[str] = field(default_factory=list)
    rejected_claim_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason_codes": list(self.reason_codes),
            "durable_signals": list(self.durable_signals),
            "accepted_claim_ids": list(self.accepted_claim_ids),
            "rejected_claim_ids": list(self.rejected_claim_ids),
        }


class RuleReflectionSynthesizer:
    """Build conservative claim candidates from normalized TaskEvidence."""

    def synthesize(self, task_description: str, evidence: TaskEvidence) -> ReflectionCandidate:
        claims: list[ReflectionClaim] = []
        source_event_ids: list[str] = []

        def add_claim(
            claim_type: ClaimType,
            semantic_key: str,
            statement: str,
            evidence_ids: list[str] | tuple[str, ...],
            epistemic_status: Literal["confirmed", "inferred", "unknown"],
            *,
            applies_when: str = "",
            limitations: list[str] | None = None,
            verification_ids: list[str] | None = None,
            related_error_ids: list[str] | None = None,
            related_recovery_ids: list[str] | None = None,
        ) -> None:
            bounded_ids = _ordered_unique(list(evidence_ids))
            claims.append(
                ReflectionClaim(
                    claim_id=f"claim-{len(claims) + 1:06d}",
                    claim_type=claim_type,
                    semantic_key=semantic_key,
                    statement=sanitize_evidence_text(statement, CLAIM_MAX_TEXT_CHARS),
                    evidence_ids=bounded_ids,
                    epistemic_status=epistemic_status,
                    applies_when=sanitize_evidence_text(applies_when, CLAIM_MAX_AUX_TEXT_CHARS),
                    limitations=[
                        sanitize_evidence_text(item, CLAIM_MAX_AUX_TEXT_CHARS)
                        for item in (limitations or [])[:CLAIM_MAX_ITEMS]
                    ],
                    verification_ids=_ordered_unique(verification_ids or []),
                    related_error_ids=_ordered_unique(related_error_ids or []),
                    related_recovery_ids=_ordered_unique(related_recovery_ids or []),
                )
            )
            source_event_ids.extend(bounded_ids)

        config_constraints = [
            item for item in evidence.decisions if item.source_kind == "config_constraint"
        ]
        for decision in evidence.decisions:
            self._synthesize_decision_claim(
                decision,
                task_description,
                evidence,
                add_claim,
            )

        confirmed_libraries = [
            item for item in evidence.libraries if item.status == "confirmed"
        ]
        routine_ruff_format = (
            {item.name for item in confirmed_libraries} == {"ruff"}
            and bool(evidence.files_changed)
            and not any(
                (
                    evidence.files_read,
                    evidence.errors,
                    evidence.recoveries,
                    evidence.decisions,
                )
            )
        )
        if confirmed_libraries and not config_constraints and not routine_ruff_format:
            names = sorted(dict.fromkeys(item.name for item in confirmed_libraries))
            library_ids = _ordered_unique(
                [event_id for item in confirmed_libraries for event_id in item.event_ids]
            )
            add_claim(
                "dependency",
                _semantic_slug("_".join(names), "project_dependencies"),
                f"Project confirmed dependencies: {', '.join(names)}.",
                library_ids,
                "confirmed",
            )

        recovery_error_ids = {
            error_id for recovery in evidence.recoveries for error_id in recovery.related_error_ids
        }
        for recovery in evidence.recoveries:
            self._synthesize_recovery_claim(recovery, evidence, add_claim)

        root_cause_error_ids = {
            error_id
            for claim in claims
            if claim.claim_type == "root_cause"
            for error_id in claim.related_error_ids
        }
        for error in evidence.errors:
            if error.error_id in recovery_error_ids or error.error_id in root_cause_error_ids:
                continue
            if not self._specific_error(error):
                continue
            add_claim(
                "error_pattern",
                _semantic_slug(
                    f"{error.tool_name or 'tool'} {error.error_type or ''} {error.message}",
                    "error",
                ),
                self._error_statement(error),
                error.source_event_ids,
                error.epistemic_status,
                applies_when=self._error_applies_when(error),
                limitations=["Observed in one task trace; broader recurrence is not yet established."],
                related_error_ids=[error.error_id],
            )

        return ReflectionCandidate(
            task_summary=sanitize_evidence_text(task_description, 200),
            outcome=evidence.outcome,
            claims=claims,
            source_event_ids=_ordered_unique(source_event_ids),
            synthesis_diagnostics=[],
        )

    def _synthesize_decision_claim(
        self,
        decision: DecisionEvidence,
        task_description: str,
        evidence: TaskEvidence,
        add_claim: Any,
    ) -> None:
        statement = decision.statement.strip()
        if not statement or decision.source_kind == "inferred_rationale":
            return
        lowered = statement.lower()
        if decision.source_kind == "user_correction":
            add_claim(
                "correction",
                _semantic_slug(statement, "correction"),
                f"User correction: {statement}",
                decision.event_ids,
                decision.epistemic_status,
            )
            return
        if decision.source_kind in {"user_constraint", "config_constraint"}:
            if re.search(r"\b(?:verify|verification|test|check)\b|(?:验证|测试|检查)", lowered):
                passed = [item for item in evidence.verification if item.result == "passed"]
                if passed:
                    verification_ids = [item.verification_id for item in passed]
                    event_ids = list(decision.event_ids) + [
                        event_id for item in passed for event_id in item.event_ids
                    ]
                    add_claim(
                        "verification_rule",
                        _semantic_slug(statement, "verification_rule"),
                        f"Project verification rule: {statement}",
                        event_ids,
                        decision.epistemic_status,
                        applies_when=f"When {sanitize_evidence_text(task_description, 180)}.",
                        verification_ids=verification_ids,
                    )
                    return
            add_claim(
                "constraint",
                _semantic_slug(statement, "project_constraint"),
                f"Project constraint: {statement}",
                decision.event_ids,
                decision.epistemic_status,
            )
            return

        if re.search(r"\b(?:root cause|caused|causes)\b|(?:根因|导致)", lowered):
            related_errors = list(evidence.errors)
            related_recoveries = [
                recovery
                for recovery in evidence.recoveries
                if set(recovery.related_error_ids)
                & {error.error_id for error in related_errors}
            ]
            last_recovery = related_recoveries[-1] if related_recoveries else None
            passed = self._passed_verifications_after(last_recovery, evidence)
            linked_passed = [
                item
                for item in passed
                if last_recovery is not None
                and self._verification_matches_recovery(
                    item, last_recovery, related_errors, evidence
                )
            ]
            confirmed = bool(related_errors and related_recoveries and linked_passed)
            status: Literal["confirmed", "inferred", "unknown"] = (
                "confirmed"
                if confirmed and decision.epistemic_status == "confirmed"
                else "inferred"
            )
            limitations = []
            if status != "confirmed":
                limitations.append("The causal explanation is incomplete or lacks linked recovery verification.")
            if any(item.scope == "targeted" for item in linked_passed):
                limitations.append("The recovery was checked only by targeted verification.")
            event_ids = list(decision.event_ids)
            event_ids.extend(event_id for error in related_errors for event_id in error.source_event_ids)
            event_ids.extend(event_id for recovery in related_recoveries for event_id in recovery.event_ids)
            event_ids.extend(event_id for item in linked_passed for event_id in item.event_ids)
            add_claim(
                "root_cause",
                _semantic_slug(statement, "root_cause"),
                statement,
                event_ids,
                status,
                applies_when=self._error_applies_when(related_errors[0]) if related_errors else f"When {task_description}.",
                limitations=limitations,
                verification_ids=[item.verification_id for item in linked_passed],
                related_error_ids=[error.error_id for error in related_errors],
                related_recovery_ids=[item.recovery_id for item in related_recoveries],
            )
            return

        limitations: list[str] = []
        rationale = decision.rationale or ""
        if decision.epistemic_status == "inferred" or re.search(
            r"\b(?:may|might|probably|likely|older|compatib)\w*\b", rationale.lower()
        ):
            limitations.append("The decision is limited to the stated compatibility context.")
        add_claim(
            "decision",
            _semantic_slug(statement, "decision"),
            statement,
            decision.event_ids,
            decision.epistemic_status,
            applies_when=f"When {sanitize_evidence_text(task_description, 180)}.",
            limitations=limitations,
        )

    def _synthesize_recovery_claim(
        self,
        recovery: RecoveryEvidence,
        evidence: TaskEvidence,
        add_claim: Any,
    ) -> None:
        related_errors = [
            item for item in evidence.errors if item.error_id in recovery.related_error_ids
        ]
        if not related_errors:
            return
        passed = self._passed_verifications_after(recovery, evidence)
        linked_passed = [
            item for item in passed if self._verification_matches_recovery(item, recovery, related_errors, evidence)
        ]
        status: Literal["confirmed", "inferred", "unknown"] = (
            "confirmed"
            if linked_passed and recovery.epistemic_status == "confirmed"
            else "inferred"
        )
        limitations: list[str] = []
        if not linked_passed:
            limitations.append("The recovery has no successful verification linked to this failure.")
        elif any(item.scope == "targeted" for item in linked_passed):
            limitations.append("The recovery was checked only by targeted verification.")
        event_ids = [
            event_id for error in related_errors for event_id in error.source_event_ids
        ] + list(recovery.event_ids) + [
            event_id for item in linked_passed for event_id in item.event_ids
        ]
        error = related_errors[0]
        add_claim(
            "recovery",
            _semantic_slug(f"{recovery.action} {error.message}", "recovery"),
            f"After {error.message}, the recovery action was: {recovery.action}.",
            event_ids,
            status,
            applies_when=self._error_applies_when(error),
            limitations=limitations,
            verification_ids=[item.verification_id for item in linked_passed],
            related_error_ids=[item.error_id for item in related_errors],
            related_recovery_ids=[recovery.recovery_id],
        )

    def _passed_verifications_after(
        self,
        recovery: RecoveryEvidence | None,
        evidence: TaskEvidence,
    ) -> list[VerificationEvidence]:
        recovery_position = _event_position(evidence, recovery.event_ids) if recovery else -1
        return [
            item
            for item in evidence.verification
            if item.result == "passed"
            and (
                recovery_position < 0
                or _event_position(evidence, item.event_ids) < 0
                or _event_position(evidence, item.event_ids) > recovery_position
            )
        ]

    def _verification_matches_recovery(
        self,
        verification: VerificationEvidence,
        recovery: RecoveryEvidence,
        errors: list[ErrorEvidence],
        evidence: TaskEvidence,
    ) -> bool:
        if verification.scope == "full":
            return True
        recovery_text = " ".join(
            [recovery.action, *recovery.files_changed, *(item.message for item in errors)]
        )
        verification_files = [
            item.path
            for item in evidence.referenced_files
            if verification.call_id and item.call_id == verification.call_id
        ]
        verification_text = " ".join([verification.summary, *verification_files])
        if _text_tokens(recovery_text) & _text_tokens(verification_text):
            return True
        return bool(_cjk_bigrams(recovery_text) & _cjk_bigrams(verification_text))

    def _specific_error(self, error: ErrorEvidence) -> bool:
        message = _normalize_text(error.message)
        if not message or message in _GENERIC_ERROR_MESSAGES:
            return False
        if error.error_type and _normalize_text(error.error_type) not in {"toolerror", "unknownerror"}:
            return True
        return bool(
            re.search(r"[/\\._:]|\b(?:denied|missing|mismatch|timeout|timed out|unavailable|accepted)\b", message)
            or len(message) >= 32
            or re.search(r"[\u4e00-\u9fff]{4,}", message)
        )

    def _error_statement(self, error: ErrorEvidence) -> str:
        prefix = " / ".join(
            item for item in (error.tool_name, error.error_type) if item
        )
        return f"Observed error pattern for {prefix}: {error.message}" if prefix else f"Observed error pattern: {error.message}"

    def _error_applies_when(self, error: ErrorEvidence) -> str:
        source = error.tool_name or "the operation"
        signal = error.error_type or error.message[:160]
        return f"When {source} reports {signal}."


class ReflectionClaimValidator:
    """Validate candidate claims through indexed evidence provenance."""

    _APPLICABILITY_REQUIRED = {
        "error_pattern",
        "root_cause",
        "recovery",
        "decision",
        "verification_rule",
        "warning",
    }

    def validate(
        self,
        candidate: ReflectionCandidate,
        evidence: TaskEvidence,
    ) -> ClaimValidationResult:
        indexes = self._build_indexes(evidence)
        valid: list[ReflectionClaim] = []
        rejected: list[ReflectionClaim] = []
        issues: list[ClaimValidationIssue] = []

        for group in self._claim_groups(candidate.claims):
            merged, conflict_issue = self._merge_group(group)
            if conflict_issue is not None and conflict_issue.severity == "error":
                rejected.extend(group)
                issues.append(conflict_issue)
                continue
            if conflict_issue is not None:
                issues.append(conflict_issue)
            claim, redaction_issue = self._sanitize_claim(merged)
            if redaction_issue is not None:
                issues.append(redaction_issue)
            claim_issues = self._validate_claim(claim, indexes)
            if any(issue.severity == "error" for issue in claim_issues):
                rejected.append(claim)
            else:
                valid.append(claim)
            issues.extend(claim_issues)

        return ClaimValidationResult(valid_claims=valid, rejected_claims=rejected, issues=issues)

    def _claim_groups(self, claims: list[ReflectionClaim]) -> list[list[ReflectionClaim]]:
        groups: dict[str, list[ReflectionClaim]] = {}
        for claim in claims[:CLAIM_MAX_ITEMS]:
            groups.setdefault(_normalize_semantic_key(claim.semantic_key), []).append(claim)
        return list(groups.values())

    def _merge_group(
        self, group: list[ReflectionClaim]
    ) -> tuple[ReflectionClaim, ClaimValidationIssue | None]:
        first = group[0]
        signatures = {
            (
                claim.claim_type,
                _normalize_text(claim.statement),
                claim.epistemic_status,
                _normalize_text(claim.applies_when),
                tuple(_normalize_text(item) for item in claim.limitations),
            )
            for claim in group
        }
        if len(signatures) > 1:
            return first, ClaimValidationIssue(
                code="conflicting_semantic_key",
                message=f"Conflicting claims use semantic_key {first.semantic_key}.",
                claim_id=first.claim_id,
            )
        if len(group) == 1:
            return first, None
        return replace(
            first,
            evidence_ids=_ordered_unique(
                [event_id for claim in group for event_id in claim.evidence_ids]
            ),
            verification_ids=_ordered_unique(
                [item for claim in group for item in claim.verification_ids]
            ),
            related_error_ids=_ordered_unique(
                [item for claim in group for item in claim.related_error_ids]
            ),
            related_recovery_ids=_ordered_unique(
                [item for claim in group for item in claim.related_recovery_ids]
            ),
        ), ClaimValidationIssue(
            code="duplicate_semantic_key_merged",
            message=f"Merged duplicate semantic_key {first.semantic_key}.",
            claim_id=first.claim_id,
            severity="info",
        )

    def _sanitize_claim(
        self, claim: ReflectionClaim
    ) -> tuple[ReflectionClaim, ClaimValidationIssue | None]:
        sanitized_statement = sanitize_evidence_text(claim.statement, CLAIM_MAX_TEXT_CHARS)
        sanitized_applies = sanitize_evidence_text(claim.applies_when, CLAIM_MAX_AUX_TEXT_CHARS)
        sanitized_key = _normalize_semantic_key(claim.semantic_key)
        sanitized_limitations = [
            sanitize_evidence_text(item, CLAIM_MAX_AUX_TEXT_CHARS)
            for item in claim.limitations[:CLAIM_MAX_ITEMS]
        ]
        sanitized = replace(
            claim,
            semantic_key=sanitized_key,
            statement=sanitized_statement,
            evidence_ids=_ordered_unique(claim.evidence_ids),
            applies_when=sanitized_applies,
            limitations=sanitized_limitations,
            verification_ids=_ordered_unique(claim.verification_ids),
            related_error_ids=_ordered_unique(claim.related_error_ids),
            related_recovery_ids=_ordered_unique(claim.related_recovery_ids),
        )
        changed = (
            sanitized_statement != claim.statement
            or sanitized_applies != claim.applies_when
            or sanitized_limitations != claim.limitations
        )
        if not changed:
            return sanitized, None
        return sanitized, ClaimValidationIssue(
            code="claim_text_redacted_or_bounded",
            message="Claim text was redacted or length-bounded before validation.",
            claim_id=claim.claim_id,
            severity="warning",
        )

    def _build_indexes(self, evidence: TaskEvidence) -> dict[str, Any]:
        event_types: dict[str, set[str]] = defaultdict(set)
        decisions_by_event: dict[str, list[DecisionEvidence]] = defaultdict(list)
        libraries_by_event: dict[str, list[Any]] = defaultdict(list)
        errors_by_event: dict[str, list[ErrorEvidence]] = defaultdict(list)
        recoveries_by_event: dict[str, list[RecoveryEvidence]] = defaultdict(list)
        errors_by_id = {item.error_id: item for item in evidence.errors}
        recoveries_by_id = {item.recovery_id: item for item in evidence.recoveries}
        verification_by_id = {item.verification_id: item for item in evidence.verification}

        for item in evidence.files_read + evidence.files_changed + evidence.referenced_files:
            for event_id in item.event_ids:
                event_types[event_id].add("file")
        for item in evidence.libraries:
            for event_id in item.event_ids:
                event_types[event_id].add("library")
                libraries_by_event[event_id].append(item)
        for item in evidence.errors:
            for event_id in item.source_event_ids:
                event_types[event_id].add("error")
                errors_by_event[event_id].append(item)
        for item in evidence.recoveries:
            for event_id in item.event_ids:
                event_types[event_id].add("recovery")
                recoveries_by_event[event_id].append(item)
        for item in evidence.decisions:
            for event_id in item.event_ids:
                event_types[event_id].add("decision")
                decisions_by_event[event_id].append(item)
        for item in evidence.verification:
            for event_id in item.event_ids:
                event_types[event_id].add("verification")
        return {
            "event_types": event_types,
            "decisions_by_event": decisions_by_event,
            "libraries_by_event": libraries_by_event,
            "errors_by_event": errors_by_event,
            "recoveries_by_event": recoveries_by_event,
            "errors_by_id": errors_by_id,
            "recoveries_by_id": recoveries_by_id,
            "verification_by_id": verification_by_id,
        }

    def _validate_claim(
        self, claim: ReflectionClaim, indexes: dict[str, Any]
    ) -> list[ClaimValidationIssue]:
        issues: list[ClaimValidationIssue] = []

        def reject(code: str, message: str) -> None:
            issues.append(ClaimValidationIssue(code, message, claim.claim_id))

        if not claim.evidence_ids:
            reject("missing_evidence_reference", "Claim has no evidence_ids.")
        missing_events = [
            event_id for event_id in claim.evidence_ids if event_id not in indexes["event_types"]
        ]
        if missing_events:
            reject("invalid_evidence_reference", f"Unknown evidence_ids: {missing_events}.")
        missing_verification = [
            item for item in claim.verification_ids if item not in indexes["verification_by_id"]
        ]
        if missing_verification:
            reject("invalid_verification_reference", f"Unknown verification_ids: {missing_verification}.")
        missing_errors = [
            item for item in claim.related_error_ids if item not in indexes["errors_by_id"]
        ]
        if missing_errors:
            reject("invalid_error_reference", f"Unknown related_error_ids: {missing_errors}.")
        missing_recoveries = [
            item for item in claim.related_recovery_ids if item not in indexes["recoveries_by_id"]
        ]
        if missing_recoveries:
            reject("invalid_recovery_reference", f"Unknown related_recovery_ids: {missing_recoveries}.")

        referenced_types = set().union(
            *(indexes["event_types"].get(event_id, set()) for event_id in claim.evidence_ids)
        ) if claim.evidence_ids else set()
        self._validate_groundedness(claim, referenced_types, indexes, reject)
        self._validate_statement_alignment(claim, indexes, reject)

        if claim.epistemic_status == "unknown":
            reject("unknown_epistemic_status", "Unknown claims cannot be persisted as facts.")
        source_statuses = self._source_statuses(claim, indexes)
        if claim.epistemic_status == "confirmed" and any(
            status != "confirmed" for status in source_statuses
        ):
            reject("epistemic_status_overclaim", "Claim certainty exceeds its source evidence.")

        normalized = _normalize_text(claim.statement)
        if not normalized or any(phrase in normalized for phrase in _GENERIC_CLAIMS):
            reject("generic_claim", "Claim is a generic execution summary, not reusable knowledge.")
        elif len(normalized) < 16:
            reject("insufficient_specificity", "Claim does not identify a specific object or action.")

        if claim.claim_type in self._APPLICABILITY_REQUIRED and not claim.applies_when.strip():
            reject("missing_applies_when", f"{claim.claim_type} requires applies_when.")
        if claim.epistemic_status == "inferred" and not claim.limitations:
            reject("missing_limitations", "Inferred claims require limitations.")
        if claim.claim_type in {"recovery", "root_cause"}:
            targeted = any(
                indexes["verification_by_id"].get(item)
                and indexes["verification_by_id"][item].scope == "targeted"
                for item in claim.verification_ids
            )
            if targeted and not claim.limitations:
                reject("missing_limitations", "Targeted verification requires limitations.")

        from minicode.memory import assess_memory_safety

        safety = assess_memory_safety(claim.statement, source="reflection_claim")
        if not safety.allowed:
            reject("unsafe_claim_text", "Claim text contains an unsafe future instruction.")
        return issues

    def _validate_groundedness(
        self,
        claim: ReflectionClaim,
        referenced_types: set[str],
        indexes: dict[str, Any],
        reject: Any,
    ) -> None:
        required: dict[str, set[str]] = {
            "constraint": {"decision"},
            "dependency": {"library"},
            "error_pattern": {"error"},
            "root_cause": {"error", "decision"},
            "recovery": {"error", "recovery"},
            "decision": {"decision"},
            "correction": {"decision"},
            "verification_rule": {"verification", "decision"},
            "warning": {"error"},
        }
        missing = required[claim.claim_type] - referenced_types
        if missing:
            reject("claim_type_evidence_mismatch", f"{claim.claim_type} lacks evidence types {sorted(missing)}.")

        decisions = [
            item
            for event_id in claim.evidence_ids
            for item in indexes["decisions_by_event"].get(event_id, [])
        ]
        if claim.claim_type == "constraint" and not any(
            item.source_kind in {"user_constraint", "config_constraint"}
            for item in decisions
        ):
            reject("constraint_not_stable", "Constraint lacks user or config provenance.")
        if claim.claim_type == "dependency":
            libraries = [
                item
                for event_id in claim.evidence_ids
                for item in indexes["libraries_by_event"].get(event_id, [])
            ]
            if not libraries or any(item.status != "confirmed" for item in libraries):
                reject("dependency_not_confirmed", "Dependency lacks confirmed LibraryEvidence.")
        if claim.claim_type == "correction" and not any(
            item.source_kind in {"user_correction", "old_memory_disproved"}
            for item in decisions
        ):
            reject("correction_not_explicit", "Correction lacks explicit correction provenance.")
        if claim.claim_type == "verification_rule" and not any(
            item.source_kind in {"user_constraint", "config_constraint"}
            for item in decisions
        ):
            reject("verification_rule_not_stable", "Verification rule lacks a stable rule source.")

        passed = [
            indexes["verification_by_id"].get(item) for item in claim.verification_ids
        ]
        has_passed = any(item and item.result == "passed" for item in passed)
        if claim.claim_type == "recovery" and claim.epistemic_status == "confirmed" and not has_passed:
            reject("confirmed_recovery_without_verification", "Confirmed recovery requires passed verification.")
        if claim.claim_type == "root_cause" and claim.epistemic_status == "confirmed":
            if not claim.related_error_ids or not claim.related_recovery_ids or not has_passed:
                reject("confirmed_root_cause_without_full_chain", "Confirmed root cause requires error, recovery, and passed verification.")

    def _validate_statement_alignment(
        self,
        claim: ReflectionClaim,
        indexes: dict[str, Any],
        reject: Any,
    ) -> None:
        """Prevent a valid evidence ID from endorsing unrelated claim text."""
        statement = _normalize_text(claim.statement)

        def source_text(value: str) -> str:
            return _normalize_text(sanitize_evidence_text(value, CLAIM_MAX_TEXT_CHARS))

        decisions = [
            item
            for event_id in claim.evidence_ids
            for item in indexes["decisions_by_event"].get(event_id, [])
        ]
        libraries = [
            item
            for event_id in claim.evidence_ids
            for item in indexes["libraries_by_event"].get(event_id, [])
        ]
        errors = [
            item
            for event_id in claim.evidence_ids
            for item in indexes["errors_by_event"].get(event_id, [])
        ]
        recoveries = [
            item
            for event_id in claim.evidence_ids
            for item in indexes["recoveries_by_event"].get(event_id, [])
        ]

        aligned = True
        if claim.claim_type in {"constraint", "decision", "correction", "verification_rule", "root_cause"}:
            aligned = any(
                source_text(item.statement) in statement for item in decisions
            )
        elif claim.claim_type == "dependency":
            names = {source_text(item.name) for item in libraries if item.status == "confirmed"}
            aligned = bool(names) and all(name in statement for name in names)
        elif claim.claim_type in {"error_pattern", "warning"}:
            aligned = any(source_text(item.message) in statement for item in errors)
        elif claim.claim_type == "recovery":
            aligned = (
                any(source_text(item.action) in statement for item in recoveries)
                and any(source_text(item.message) in statement for item in errors)
            )
        if not aligned:
            reject(
                "claim_statement_not_grounded",
                "Claim statement is not aligned with its referenced structured evidence.",
            )

    def _source_statuses(self, claim: ReflectionClaim, indexes: dict[str, Any]) -> list[str]:
        statuses: list[str] = []
        claim_statement = _normalize_text(claim.statement)
        for event_id in claim.evidence_ids:
            statuses.extend(
                item.epistemic_status
                for item in indexes["decisions_by_event"].get(event_id, [])
                if _normalize_text(
                    sanitize_evidence_text(item.statement, CLAIM_MAX_TEXT_CHARS)
                )
                in claim_statement
                or claim_statement
                in _normalize_text(
                    sanitize_evidence_text(item.statement, CLAIM_MAX_TEXT_CHARS)
                )
            )
            statuses.extend(
                item.epistemic_status
                for item in indexes["libraries_by_event"].get(event_id, [])
            )
        statuses.extend(
            indexes["errors_by_id"][item].epistemic_status
            for item in claim.related_error_ids
            if item in indexes["errors_by_id"]
        )
        statuses.extend(
            indexes["recoveries_by_id"][item].epistemic_status
            for item in claim.related_recovery_ids
            if item in indexes["recoveries_by_id"]
        )
        return statuses


class ReflectionValueGate:
    """Select validated reflections that contain durable reusable signals."""

    def evaluate(
        self,
        candidate: ReflectionCandidate,
        validation: ClaimValidationResult,
        evidence: TaskEvidence,
    ) -> ReflectionValueDecision:
        rejected_ids = [claim.claim_id for claim in validation.rejected_claims]
        if not validation.valid_claims:
            reasons = self._low_value_reasons(evidence)
            if candidate.claims and any(
                claim.claim_type == "root_cause" for claim in candidate.claims
            ):
                reasons.append("unsupported_root_cause")
            reasons.append("no_valid_claim")
            return ReflectionValueDecision(
                accepted=False,
                reason_codes=_ordered_unique(reasons),
                rejected_claim_ids=rejected_ids,
            )

        global_errors = [
            issue
            for issue in validation.issues
            if issue.severity == "error" and issue.claim_id is None
        ]
        if global_errors:
            return ReflectionValueDecision(
                accepted=False,
                reason_codes=["global_validation_error"],
                rejected_claim_ids=rejected_ids,
            )

        signals: list[str] = []
        accepted_claim_ids: list[str] = []
        for claim in validation.valid_claims:
            claim_signals = self._signals_for_claim(claim)
            if claim_signals:
                signals.extend(claim_signals)
                accepted_claim_ids.append(claim.claim_id)

        if not signals:
            reasons = self._low_value_reasons(evidence)
            if any(
                claim.claim_type == "root_cause"
                and claim.epistemic_status != "confirmed"
                for claim in validation.valid_claims
            ):
                reasons.append("unsupported_root_cause")
            if candidate.outcome == "unknown":
                reasons.append("unknown_outcome_without_durable_fact")
            reasons.append("no_durable_signal")
            return ReflectionValueDecision(
                accepted=False,
                reason_codes=_ordered_unique(reasons),
                rejected_claim_ids=rejected_ids,
            )

        accepted_types = {
            claim.claim_type
            for claim in validation.valid_claims
            if claim.claim_id in accepted_claim_ids
        }
        has_unverified_recovery = bool(evidence.recoveries) and any(
            recovery.epistemic_status != "confirmed"
            for recovery in evidence.recoveries
        )
        has_passed_verification = any(
            verification.result == "passed"
            for verification in evidence.verification
        )
        if (
            evidence.outcome in {"failed", "unknown"}
            and has_unverified_recovery
            and not has_passed_verification
            and accepted_types
            and accepted_types <= {"error_pattern", "warning"}
        ):
            return ReflectionValueDecision(
                accepted=False,
                reason_codes=["unverified_recovery_context"],
                rejected_claim_ids=rejected_ids,
            )

        return ReflectionValueDecision(
            accepted=True,
            reason_codes=["accepted_durable_reflection"],
            durable_signals=_ordered_unique(signals),
            accepted_claim_ids=accepted_claim_ids,
            rejected_claim_ids=rejected_ids,
        )

    def _signals_for_claim(self, claim: ReflectionClaim) -> list[str]:
        if claim.claim_type == "constraint":
            return ["stable_project_constraint"]
        if claim.claim_type == "dependency" and claim.epistemic_status == "confirmed":
            return ["confirmed_dependency"]
        if claim.claim_type in {"error_pattern", "warning"}:
            return ["reusable_error_pattern"]
        if claim.claim_type == "root_cause" and claim.epistemic_status == "confirmed":
            return ["confirmed_error_recovery_verified"]
        if claim.claim_type == "recovery" and claim.epistemic_status == "confirmed":
            return ["confirmed_error_recovery_verified", "verified_solution"]
        if claim.claim_type == "decision":
            return ["key_technical_decision"]
        if claim.claim_type == "correction":
            signals = ["user_correction"]
            normalized = _normalize_text(f"{claim.semantic_key} {claim.statement}")
            if "memory" in normalized and any(word in normalized for word in ("wrong", "invalid", "stale")):
                signals.append("old_memory_disproved")
            return signals
        if claim.claim_type == "verification_rule":
            return ["stable_verification_rule"]
        return []

    def _low_value_reasons(self, evidence: TaskEvidence) -> list[str]:
        tools = {item.tool_name for item in evidence.tool_calls}
        reasons: list[str] = []
        if tools and tools <= _READ_TOOLS:
            reasons.append("routine_read_only")
        if tools and tools <= _SEARCH_TOOLS:
            reasons.append("routine_search_only")
        if tools and tools <= _LIST_TOOLS:
            reasons.append("routine_directory_listing")
        if tools and tools <= _FORMAT_TOOLS:
            reasons.append("routine_format_only")
        if evidence.verification and not evidence.errors and not evidence.recoveries:
            reasons.append("routine_verification_only")
        if (
            {item.name for item in evidence.libraries if item.status == "confirmed"}
            == {"ruff"}
            and evidence.files_changed
            and not any(
                (
                    evidence.files_read,
                    evidence.errors,
                    evidence.recoveries,
                    evidence.decisions,
                )
            )
        ):
            reasons.append("routine_format_only")
        if evidence.recovery_suggestions and not evidence.recoveries:
            reasons.append("recovery_suggestion_only")
        if evidence.libraries and not any(
            item.status == "confirmed" for item in evidence.libraries
        ):
            reasons.append("weak_dependency_mention")
        if evidence.outcome == "unknown":
            reasons.append("unknown_outcome_without_durable_fact")
        if evidence.outcome == "success" and not any(
            (
                evidence.errors,
                evidence.recoveries,
                evidence.decisions,
                evidence.libraries,
            )
        ):
            reasons.append("task_success_only")
        if tools and not any((evidence.errors, evidence.recoveries, evidence.decisions, evidence.libraries)):
            reasons.append("tool_count_only")
        if evidence.errors and not reasons:
            reasons.append("generic_error_summary")
        return reasons or ["no_durable_signal"]


__all__ = [
    "ClaimType",
    "ClaimValidationIssue",
    "ClaimValidationResult",
    "ReflectionCandidate",
    "ReflectionClaim",
    "ReflectionClaimValidator",
    "ReflectionSynthesizer",
    "ReflectionValueDecision",
    "ReflectionValueGate",
    "RuleReflectionSynthesizer",
]
