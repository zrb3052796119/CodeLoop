"""Agent self-reflection system.

Provides post-task reflection to improve future performance:
- Success/failure analysis
- Strategy effectiveness review
- Error pattern recognition
- Memory recording for future reference
"""

from __future__ import annotations

import json
import inspect
import time
from dataclasses import dataclass, field, replace
from typing import Any

from minicode.logging_config import get_logger
from minicode.memory import MemoryApprovalPolicy, MemoryManager, MemoryScope
from minicode.reflection_evidence import (
    TaskEvidence,
    TraceEvidenceExtractor,
)
from minicode.reflection_llm import (
    AttemptingReflectionSynthesizer,
    ReflectionLLMConfig,
    ReflectionLLMEligibilityGate,
    ShadowComparisonResult,
    build_shadow_comparison,
)
from minicode.reflection_claim_selection import (
    ClaimSuppressionResult,
    PersistableClaimEvaluation,
    detect_rule_regression,
    suppress_redundant_llm_claims,
)
from minicode.reflection_synthesis import (
    ClaimValidationResult,
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionClaimValidator,
    ReflectionValueDecision,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)

logger = get_logger("agent_reflection")


@dataclass
class ReflectionResult:
    """Result of a reflection cycle."""

    task_summary: str
    success: bool
    key_decisions: list[str]
    errors_encountered: list[str]
    lessons_learned: list[str]
    suggested_improvements: list[str]
    confidence: float
    timestamp: float = field(default_factory=time.time)
    # Structured task context for domain-aware memory retrieval
    task_context: dict[str, Any] = field(default_factory=dict)
    task_evidence: TaskEvidence | None = None
    reflection_candidate: ReflectionCandidate | None = None
    claim_validation: ClaimValidationResult = field(default_factory=ClaimValidationResult)
    value_decision: ReflectionValueDecision = field(default_factory=ReflectionValueDecision)
    structured_claims: list[ReflectionClaim] = field(default_factory=list)
    synthesis_source: str = "rule"
    synthesis_fallback_reason: str | None = None
    shadow_comparison: ShadowComparisonResult | None = None
    persistable_evaluation: PersistableClaimEvaluation | None = None
    selection_strategy: str = "gap_fill"
    selection_strategy_reason: str = "configured_or_default"
    selection_source: str = "rule"
    selection_reason: str = "rule_mode"
    rule_persistable_claim_ids: list[str] = field(default_factory=list)
    llm_persistable_claim_ids: list[str] = field(default_factory=list)
    final_persistable_claim_ids: list[str] = field(default_factory=list)
    rule_regression: bool = False
    gap_fill_attempted: bool = False
    gap_fill_success: bool = False
    replace_regression: bool = False
    suppressed_claim_ids: list[str] = field(default_factory=list)
    suppression_reason_codes: dict[str, str] = field(default_factory=dict)
    _shadow_completed: bool = field(default=False, repr=False, compare=False)

    def to_memory_entry(self) -> dict[str, Any]:
        """Convert to a memory entry for persistence."""
        context = self.task_context
        # Build domain list from context files
        domains: list[str] = []
        if context.get("files"):
            try:
                from minicode.domain_classifier import get_active_domain_values
                domains = get_active_domain_values(
                    current_files=context.get("files", []),
                    intent_text=self.task_summary,
                )
            except Exception:
                pass

        return {
            "content": self._format_content(),
            "category": "task_context" if context else "reflection",
            "tags": self._build_tags(),
            "domains": domains,
            "metadata": {
                "task_summary": self.task_summary[:300],
                "confidence": self.confidence,
                "key_decisions": self.key_decisions,
                "errors": self.errors_encountered,
                "improvements": self.suggested_improvements,
                "task_context": context,
                "task_evidence": self._bounded_task_evidence_metadata(),
                "structured_reflection": self._bounded_structured_reflection_metadata(),
            },
        }

    def _bounded_task_evidence_metadata(self) -> dict[str, Any] | None:
        if self.task_evidence is None:
            return None
        for item_limit in (12, 6, 3):
            metadata = self.task_evidence.to_dict(max_items=item_limit)
            if len(json.dumps(metadata, ensure_ascii=False)) <= 32_768:
                if item_limit < 12:
                    metadata["metadata_truncated"] = True
                return metadata
        return {
            "outcome": self.task_evidence.outcome,
            "had_errors": self.task_evidence.had_errors,
            "errors_recovered": self.task_evidence.errors_recovered,
            "metadata_truncated": True,
        }

    def _bounded_structured_reflection_metadata(self) -> dict[str, Any]:
        """Serialize only validated claims and keep claim metadata below 32 KiB."""
        for claim_limit in (64, 32, 16, 8, 4, 1):
            metadata = {
                "claims": [
                    claim.to_dict() for claim in self.structured_claims[:claim_limit]
                ],
                "validation": {
                    "accepted": self.claim_validation.accepted,
                    "valid_claim_ids": [
                        claim.claim_id
                        for claim in self.claim_validation.valid_claims[:claim_limit]
                    ],
                    "rejected_claim_ids": [
                        claim.claim_id
                        for claim in self.claim_validation.rejected_claims[:claim_limit]
                    ],
                    "issues": [
                        issue.to_dict()
                        for issue in self.claim_validation.issues[:claim_limit]
                    ],
                },
                "value_decision": self.value_decision.to_dict(),
                "candidate_diagnostics": list(
                    (self.reflection_candidate.synthesis_diagnostics if self.reflection_candidate else [])[:claim_limit]
                ),
            }
            if len(json.dumps(metadata, ensure_ascii=False)) <= 32_768:
                if claim_limit < 64:
                    metadata["metadata_truncated"] = True
                return metadata
        return {
            "claims": [],
            "validation": {
                "accepted": False,
                "valid_claim_ids": [],
                "rejected_claim_ids": [],
                "issues": [],
            },
            "value_decision": {
                "accepted": self.value_decision.accepted,
                "reason_codes": self.value_decision.reason_codes[:8],
            },
            "metadata_truncated": True,
        }

    def _build_tags(self) -> list[str]:
        tags = ["self-reflection"]
        if self.success:
            tags.append("success")
        else:
            tags.append("failure")
        ctx = self.task_context
        if ctx.get("libraries"):
            tags.extend(ctx["libraries"])
        if ctx.get("tools"):
            tags.extend(ctx["tools"])
        return tags

    def _format_content(self) -> str:
        if self.reflection_candidate is not None:
            parts: list[str] = []
            for claim in self.structured_claims:
                if parts:
                    parts.append("")
                parts.append(claim.statement)
                if claim.applies_when:
                    parts.append(f"Applies when: {claim.applies_when}")
                if claim.limitations:
                    parts.append(f"Limitations: {'; '.join(claim.limitations)}")
            return "\n".join(parts)

        # Readable legacy adapter. Legacy results are default-denied by the
        # value gate and cannot be auto-persisted through MemoryPipeline.
        parts = [
            f"Task Context: {self.task_summary}",
        ]
        ctx = self.task_context
        if ctx.get("files"):
            parts.append(f"Files: {', '.join(ctx['files'][:8])}")
        if ctx.get("libraries"):
            parts.append(f"Libraries/Tools: {', '.join(ctx['libraries'])}")
        if ctx.get("project_state"):
            parts.append(f"State: {ctx['project_state']}")
        parts.extend(["", "Key Decisions:"])
        for d in self.key_decisions:
            parts.append(f"  - {d}")

        if self.errors_encountered:
            parts.extend(["", "Errors Encountered:"])
            for e in self.errors_encountered:
                parts.append(f"  - {e}")

        parts.extend(["", "Lessons Learned:"])
        for lesson in self.lessons_learned:
            parts.append(f"  - {lesson}")

        return "\n".join(parts)


@dataclass
class _LLMPathResult:
    comparison: ShadowComparisonResult
    candidate: ReflectionCandidate | None = None
    validation: ClaimValidationResult | None = None
    value_decision: ReflectionValueDecision | None = None
    persistable_evaluation: PersistableClaimEvaluation | None = None
    fallback_reason: str | None = None
    replace_regression: bool = False


class ReflectionEngine:
    """Engine for agent self-reflection."""

    def __init__(
        self,
        memory_manager: MemoryManager | None = None,
        min_confidence_threshold: float = 0.5,
        persist_reflections: bool = False,
        llm_config: ReflectionLLMConfig | None = None,
        llm_synthesizer: AttemptingReflectionSynthesizer | None = None,
        llm_unavailable_reason: str | None = None,
        shadow_metrics_recorder: Any | None = None,
    ):
        self.memory = memory_manager
        self.min_confidence = min_confidence_threshold
        self.persist_reflections = persist_reflections
        self._evidence_extractor = TraceEvidenceExtractor()
        self._synthesizer = RuleReflectionSynthesizer()
        self._claim_validator = ReflectionClaimValidator()
        self._value_gate = ReflectionValueGate()
        self._llm_config = llm_config or ReflectionLLMConfig()
        self._llm_synthesizer = llm_synthesizer
        self._llm_unavailable_reason = llm_unavailable_reason or "model_unavailable"
        self._llm_eligibility = ReflectionLLMEligibilityGate()
        self._shadow_metrics_recorder = shadow_metrics_recorder

    def reflect(
        self,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        metrics: Any | None = None,
        *,
        defer_shadow: bool = False,
        agent_budget: Any | None = None,
        event_sink: Any | None = None,
        cancellation_token: Any | None = None,
        deadline_monotonic: float | None = None,
    ) -> ReflectionResult:
        """Generate reflection from execution trace.

        Args:
            task_description: Original task
            execution_trace: List of step records (tool calls, responses, errors)
            metrics: Optional metrics collector for performance data

        Returns:
            Reflection result
        """
        evidence = self._evidence_extractor.extract(task_description, execution_trace)
        tool_calls = [
            {
                "tool_name": item.tool_name,
                "call_id": item.call_id,
                "status": item.status,
            }
            for item in evidence.tool_calls
        ]
        errors = [
            {
                "tool_name": item.tool_name,
                "call_id": item.call_id,
                "error_type": item.error_type,
                "message": item.message,
            }
            for item in evidence.errors
        ]
        assistant_msgs = [
            s for s in execution_trace
            if isinstance(s, dict) and s.get("type") in ("assistant", "assistant_step")
        ]
        recoveries = [
            {
                "action": item.action,
                "files": list(item.files_changed),
            }
            for item in evidence.recoveries
        ]
        task_results = [
            s for s in execution_trace
            if isinstance(s, dict) and s.get("type") == "task_result"
        ]
        success = evidence.outcome == "success"

        task_context = self._task_context_from_evidence(evidence)
        error_list = self._extract_errors(errors)
        rule_candidate = self._synthesizer.synthesize(task_description, evidence)
        rule_validation = self._claim_validator.validate(rule_candidate, evidence)
        rule_value = self._value_gate.evaluate(
            rule_candidate, rule_validation, evidence
        )
        rule_evaluation = PersistableClaimEvaluation.from_pipeline(
            rule_candidate,
            rule_validation,
            rule_value,
            selection_source="rule",
            selection_reason="rule_mode",
        )
        candidate = rule_candidate
        validation = rule_validation
        value_decision = rule_value
        final_evaluation = rule_evaluation
        synthesis_source = "rule"
        selection_source = "rule"
        selection_reason = "rule_mode"
        selection_strategy = (
            self._llm_config.selection_strategy
            if self._llm_config.mode != "rule"
            else "gap_fill"
        )
        fallback_reason: str | None = None
        comparison: ShadowComparisonResult | None = None
        llm_persistable_claim_ids: list[str] = []
        gap_fill_attempted = False
        gap_fill_success = False
        replace_regression = False
        suppressed_claim_ids: list[str] = []
        suppression_reason_codes: dict[str, str] = {}
        if self._llm_config.mode == "llm":
            if (
                selection_strategy == "gap_fill"
                and rule_evaluation.persistable_claims
            ):
                selection_reason = "rule_already_durable"
            else:
                gap_fill_attempted = selection_strategy == "gap_fill"
                path = self._run_llm_path(
                    task_description,
                    evidence,
                    rule_candidate,
                    rule_validation,
                    rule_value,
                    agent_budget,
                    event_sink=event_sink,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
                comparison = path.comparison
                fallback_reason = path.fallback_reason
                replace_regression = path.replace_regression
                if path.persistable_evaluation is not None:
                    llm_persistable_claim_ids = [
                        claim.claim_id
                        for claim in path.persistable_evaluation.persistable_claims
                    ]
                    suppressed_claim_ids = [
                        claim.claim_id
                        for claim in path.persistable_evaluation.suppressed_claims
                    ]
                    suppression_reason_codes = dict(
                        path.persistable_evaluation.suppression_reason_codes
                    )
                if (
                    path.candidate is not None
                    and path.validation is not None
                    and path.value_decision is not None
                    and path.persistable_evaluation is not None
                    and path.persistable_evaluation.persistable_claims
                ):
                    candidate = path.candidate
                    validation = path.validation
                    value_decision = path.value_decision
                    final_evaluation = path.persistable_evaluation
                    fallback_reason = None
                    if selection_strategy == "gap_fill":
                        synthesis_source = "llm_gap_fill"
                        selection_source = "llm_gap_fill"
                        selection_reason = "llm_filled_rule_gap"
                        gap_fill_success = True
                    else:
                        synthesis_source = "llm_replace"
                        selection_source = "llm_replace"
                        selection_reason = "llm_replaced_rule"
                else:
                    synthesis_source = "rule_fallback"
                    selection_reason = fallback_reason or "llm_not_persistable"
        elif self._llm_config.mode == "llm_shadow":
            selection_reason = "shadow_production_rule"

        structured_claims = list(final_evaluation.persistable_claims)
        decision_types = {"constraint", "decision", "correction", "verification_rule"}
        key_decisions = [
            claim.statement
            for claim in structured_claims
            if claim.claim_type in decision_types
        ]
        lessons = [
            claim.statement
            for claim in structured_claims
            if claim.claim_type not in decision_types
        ]
        improvements = [
            limitation
            for claim in structured_claims
            for limitation in claim.limitations
        ][:10]
        confidence = self._calculate_confidence(
            success,
            len(errors),
            tool_calls,
            assistant_msgs,
            recoveries,
            task_context,
            task_results,
        )

        reflection = ReflectionResult(
            task_summary=task_description[:200],
            success=success,
            key_decisions=key_decisions,
            errors_encountered=error_list,
            lessons_learned=lessons,
            suggested_improvements=improvements,
            confidence=confidence,
            task_context=task_context,
            task_evidence=evidence,
            reflection_candidate=candidate,
            claim_validation=validation,
            value_decision=value_decision,
            structured_claims=structured_claims,
            synthesis_source=synthesis_source,
            synthesis_fallback_reason=fallback_reason,
            shadow_comparison=comparison,
            persistable_evaluation=final_evaluation,
            selection_strategy=selection_strategy,
            selection_strategy_reason=self._llm_config.selection_strategy_reason,
            selection_source=selection_source,
            selection_reason=selection_reason,
            rule_persistable_claim_ids=[
                claim.claim_id for claim in rule_evaluation.persistable_claims
            ],
            llm_persistable_claim_ids=llm_persistable_claim_ids,
            final_persistable_claim_ids=[
                claim.claim_id for claim in structured_claims
            ],
            rule_regression=(
                selection_source == "llm_replace" and replace_regression
            ),
            gap_fill_attempted=gap_fill_attempted,
            gap_fill_success=gap_fill_success,
            replace_regression=replace_regression,
            suppressed_claim_ids=suppressed_claim_ids,
            suppression_reason_codes=suppression_reason_codes,
        )

        if (
            self.persist_reflections
            and self.memory
            and structured_claims
        ):
            from minicode.memory_pipeline import assess_trace_memory_safety

            self._persist_reflection(
                reflection,
                trace_safety=assess_trace_memory_safety(execution_trace),
            )

        if self._llm_config.mode == "llm_shadow" and not defer_shadow:
            self.complete_shadow(
                reflection,
                agent_budget=agent_budget,
                event_sink=event_sink,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )

        return reflection

    def _run_llm_path(
        self,
        task_description: str,
        evidence: TaskEvidence,
        rule_candidate: ReflectionCandidate,
        rule_validation: ClaimValidationResult,
        rule_value: ReflectionValueDecision,
        agent_budget: Any | None = None,
        *,
        event_sink: Any | None = None,
        cancellation_token: Any | None = None,
        deadline_monotonic: float | None = None,
    ) -> _LLMPathResult:
        eligibility = self._llm_eligibility.evaluate(
            evidence,
            model_call_allowed=self._llm_synthesizer is not None,
            unavailable_reason=self._llm_unavailable_reason,
        )
        attempt = None
        llm_candidate = None
        llm_validation = None
        llm_production_validation = None
        llm_value = None
        suppression = ClaimSuppressionResult()
        persistable_evaluation = None
        fallback_reason = eligibility.reason_codes[0] if not eligibility.eligible else None
        sampled = eligibility.eligible
        if eligibility.eligible and self._llm_config.mode == "llm_shadow":
            from minicode.reflection_shadow_metrics import (
                deterministic_shadow_sample,
                reflection_task_identifier,
            )

            sampled = deterministic_shadow_sample(
                reflection_task_identifier(task_description),
                self._llm_config.shadow_sample_rate,
            )
            if not sampled:
                fallback_reason = "sampled_out"
        if eligibility.eligible and sampled and self._llm_synthesizer is not None:
            attempt = self._attempt_synthesis(
                task_description,
                evidence,
                agent_budget=agent_budget,
                event_sink=event_sink,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            fallback_reason = (
                attempt.failure_code if not attempt.success else None
            )
            if attempt is not None and attempt.success and attempt.candidate is not None:
                llm_candidate = attempt.candidate
                llm_validation = self._claim_validator.validate(
                    llm_candidate, evidence
                )
                suppression = suppress_redundant_llm_claims(
                    llm_validation.valid_claims,
                    evidence,
                )
                llm_production_validation = ClaimValidationResult(
                    valid_claims=list(suppression.kept_claims),
                    rejected_claims=list(llm_validation.rejected_claims),
                    issues=list(llm_validation.issues),
                )
                llm_value = self._value_gate.evaluate(
                    llm_candidate, llm_production_validation, evidence
                )
                persistable_evaluation = PersistableClaimEvaluation.from_pipeline(
                    llm_candidate,
                    llm_validation,
                    llm_value,
                    selection_source="llm",
                    selection_reason="llm_branch_evaluated",
                    suppression=suppression,
                )
                if not llm_production_validation.valid_claims:
                    fallback_reason = "all_llm_claims_rejected"
                elif not llm_value.accepted:
                    fallback_reason = "llm_value_rejected"
                elif not persistable_evaluation.persistable_claims:
                    fallback_reason = "no_accepted_llm_claims"
        rule_evaluation = PersistableClaimEvaluation.from_pipeline(
            rule_candidate,
            rule_validation,
            rule_value,
            selection_source="rule",
            selection_reason="rule_branch_evaluated",
        )
        replace_regression = bool(
            persistable_evaluation
            and persistable_evaluation.persistable_claims
            and detect_rule_regression(
                rule_evaluation.persistable_claims,
                persistable_evaluation.persistable_claims,
                evidence,
            )
        )
        comparison = build_shadow_comparison(
            task_description,
            eligibility,
            rule_candidate,
            rule_validation,
            rule_value,
            attempt=attempt,
            llm_validation=llm_validation,
            llm_value=llm_value,
            fallback_reason=fallback_reason,
            sampled=sampled,
        )
        llm_persistable_ids = tuple(
            claim.claim_id
            for claim in (
                persistable_evaluation.persistable_claims
                if persistable_evaluation
                else []
            )
        )
        rule_persistable_ids = tuple(
            claim.claim_id for claim in rule_evaluation.persistable_claims
        )
        comparison = replace(
            comparison,
            rule_persistable_claim_ids=rule_persistable_ids,
            llm_persistable_claim_ids=llm_persistable_ids,
            gap_fill_selection_source=(
                "rule"
                if rule_persistable_ids
                else ("llm_gap_fill" if llm_persistable_ids else "rule")
            ),
            replace_selection_source=(
                "llm_replace" if llm_persistable_ids else "rule"
            ),
            gap_fill_attempted=not bool(rule_persistable_ids),
            replace_regression=replace_regression,
            suppressed_claim_ids=tuple(
                claim.claim_id for claim in suppression.suppressed_claims
            ),
            suppression_reason_codes=dict(suppression.suppression_reason_codes),
        )
        return _LLMPathResult(
            comparison=comparison,
            candidate=llm_candidate,
            validation=llm_production_validation,
            value_decision=llm_value,
            persistable_evaluation=persistable_evaluation,
            fallback_reason=fallback_reason,
            replace_regression=replace_regression,
        )

    def _attempt_synthesis(
        self,
        task_description: str,
        evidence: TaskEvidence,
        **call_context: Any,
    ) -> Any:
        """Pass production controls only to synthesizers that support them."""
        assert self._llm_synthesizer is not None
        kwargs: dict[str, Any] = {}
        try:
            parameters = inspect.signature(
                self._llm_synthesizer.attempt
            ).parameters.values()
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
            names = {parameter.name for parameter in parameters}
            kwargs = {
                name: value
                for name, value in call_context.items()
                if accepts_kwargs or name in names
            }
        except (TypeError, ValueError):
            pass
        return self._llm_synthesizer.attempt(
            task_description,
            evidence,
            **kwargs,
        )

    def complete_shadow(
        self,
        reflection: ReflectionResult,
        *,
        agent_budget: Any | None = None,
        event_sink: Any | None = None,
        cancellation_token: Any | None = None,
        deadline_monotonic: float | None = None,
    ) -> ShadowComparisonResult | None:
        """Evaluate the non-production LLM branch at most once."""
        if (
            self._llm_config.mode != "llm_shadow"
            or reflection._shadow_completed
            or reflection.task_evidence is None
            or reflection.reflection_candidate is None
        ):
            return reflection.shadow_comparison
        reflection._shadow_completed = True
        path = self._run_llm_path(
            reflection.task_summary,
            reflection.task_evidence,
            reflection.reflection_candidate,
            reflection.claim_validation,
            reflection.value_decision,
            agent_budget,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )
        comparison = path.comparison
        reflection.shadow_comparison = comparison
        if path.persistable_evaluation is not None:
            reflection.llm_persistable_claim_ids = [
                claim.claim_id
                for claim in path.persistable_evaluation.persistable_claims
            ]
            reflection.suppressed_claim_ids = [
                claim.claim_id
                for claim in path.persistable_evaluation.suppressed_claims
            ]
            reflection.suppression_reason_codes = dict(
                path.persistable_evaluation.suppression_reason_codes
            )
        reflection.gap_fill_attempted = comparison.gap_fill_attempted
        reflection.gap_fill_success = bool(
            comparison.gap_fill_selection_source == "llm_gap_fill"
        )
        reflection.replace_regression = comparison.replace_regression
        if self._shadow_metrics_recorder is not None:
            try:
                self._shadow_metrics_recorder.record(comparison)
            except Exception:
                logger.info("Reflection shadow metrics recording failed safely")
        logger.info(
            "Reflection shadow: called=%s rule_valid=%d llm_valid=%d fallback=%s",
            comparison.llm_called,
            comparison.rule_valid_claim_count,
            comparison.llm_valid_claim_count,
            comparison.fallback_reason or "none",
        )
        return comparison

    def _task_context_from_evidence(self, evidence: TaskEvidence) -> dict[str, Any]:
        """Project the evidence model into the legacy task-context shape."""
        context: dict[str, Any] = {}
        files_read = list(dict.fromkeys(item.path for item in evidence.files_read))
        files_changed = list(dict.fromkeys(item.path for item in evidence.files_changed))
        referenced = list(dict.fromkeys(item.path for item in evidence.referenced_files))
        all_files = list(dict.fromkeys(files_read + files_changed))
        libraries = [
            item.name for item in evidence.libraries if item.status == "confirmed"
        ]
        tools = list(dict.fromkeys(item.tool_name for item in evidence.tool_calls))

        if all_files:
            context["files"] = all_files[:10]
        if files_read:
            context["files_read"] = files_read[:10]
        if files_changed:
            context["files_changed"] = files_changed[:10]
        if referenced:
            context["referenced_files"] = referenced[:10]
        if libraries:
            context["libraries"] = libraries[:10]
        if tools:
            context["tools"] = tools[:10]
        if evidence.errors:
            context["errors"] = [
                f"{item.error_type}: {item.message}" if item.error_type else item.message
                for item in evidence.errors[:5]
            ]
        if evidence.recoveries:
            context["recoveries"] = [item.action for item in evidence.recoveries[:5]]
        if evidence.verification:
            context["verification"] = [item.summary for item in evidence.verification[:5]]
        if evidence.outcome != "unknown":
            context["project_state"] = evidence.outcome
            context["outcome"] = evidence.outcome
        if evidence.had_errors:
            context["had_errors"] = True
            context["errors_recovered"] = evidence.errors_recovered
        return context

    def _extract_errors(self, errors: list[dict[str, Any]]) -> list[str]:
        """Format structured error events without inventing missing details."""
        formatted: list[str] = []
        for err in errors:
            message = (
                err.get("message")
                or err.get("error")
                or err.get("content")
                or err.get("output_summary")
                or ""
            )
            if not message:
                continue
            tool = err.get("tool_name") or err.get("name") or err.get("toolName") or ""
            err_type = err.get("error_type") or err.get("type_name") or ""
            prefix_parts = [p for p in (tool, err_type) if p]
            prefix = f"{' / '.join(prefix_parts)}: " if prefix_parts else ""
            formatted.append((prefix + str(message))[:240])
        return formatted[:10]

    def _extract_decisions(self, assistant_msgs: list[dict[str, Any]]) -> list[str]:
        """Extract key decisions from assistant messages."""
        decisions = []
        keywords = ["decide", "choose", "select", "use ", "will ", "plan to", "start by"]
        for msg in assistant_msgs:
            content = msg.get("content", "")
            if any(kw in content.lower() for kw in keywords):
                first_sentence = content.split(".")[0].strip()
                if len(first_sentence) > 10:
                    decisions.append(first_sentence[:200])
        return decisions[:5]

    def _generate_lessons(
        self,
        tool_calls: list[dict[str, Any]],
        errors: list[dict[str, Any]],
        recoveries: list[dict[str, Any]],
        success: bool,
    ) -> list[str]:
        """Generate lessons learned from execution."""
        lessons = []

        if success:
            lessons.append("Task completed successfully with the chosen approach.")
        else:
            lessons.append("Task encountered errors. Review error patterns for future avoidance.")

        tool_names = [
            t.get("tool_name") or t.get("name") or t.get("toolName")
            for t in tool_calls
        ]
        tool_names = [str(t) for t in tool_names if t]
        if tool_names:
            unique_tools = sorted(set(tool_names))
            lessons.append(f"Used {len(unique_tools)} unique tool(s): {', '.join(unique_tools)}.")

        if errors:
            error_tools = sorted({
                str(e.get("tool_name") or e.get("name") or e.get("toolName"))
                for e in errors
                if e.get("tool_name") or e.get("name") or e.get("toolName")
            })
            if error_tools:
                lessons.append(
                    f"Errors occurred with tool(s): {', '.join(error_tools)}. Reuse the observed recovery pattern before retrying."
                )
            for err in self._extract_errors(errors)[:3]:
                lessons.append(f"Error pattern: {err}")

        for recovery in recoveries[:3]:
            action = (
                recovery.get("action")
                or recovery.get("message")
                or recovery.get("content")
                or recovery.get("summary")
                or ""
            )
            if action:
                lessons.append(f"Recovery action: {str(action)[:220]}")

        return lessons

    def _generate_improvements(
        self,
        tool_calls: list[dict[str, Any]],
        errors: list[dict[str, Any]],
        metrics: Any | None,
    ) -> list[str]:
        """Generate improvement suggestions."""
        improvements = []

        if len(errors) > 2:
            improvements.append("High error rate detected. Consider breaking task into smaller steps.")

        if len(tool_calls) > 10:
            improvements.append("Many tool calls used. Consider more efficient approaches or better planning.")

        if metrics and hasattr(metrics, "get_summary"):
            try:
                stats = metrics.get_summary()
                if stats.get("overall_success_rate", 1.0) < 0.7:
                    improvements.append(
                        "Low success rate. Review tool usage patterns and error recovery strategies."
                    )
            except Exception:
                pass

        return improvements

    def _calculate_confidence(
        self,
        success: bool,
        error_count: int,
        tool_calls: list[dict[str, Any]],
        assistant_msgs: list[dict[str, Any]],
        recoveries: list[dict[str, Any]],
        task_context: dict[str, Any],
        task_results: list[dict[str, Any]],
    ) -> float:
        """Calculate reflection confidence score."""
        named_tools = [
            t for t in (
                call.get("tool_name") or call.get("name") or call.get("toolName")
                for call in tool_calls
            )
            if t
        ]
        base = 0.35 if success else 0.25
        evidence = 0.0
        if named_tools:
            evidence += min(len(set(named_tools)) * 0.08, 0.24)
        if task_context.get("files"):
            evidence += 0.16
        if task_context.get("errors"):
            evidence += 0.14
        if recoveries:
            evidence += 0.12
        if assistant_msgs and self._extract_decisions(assistant_msgs):
            evidence += 0.08
        if task_results:
            evidence += 0.08

        error_penalty = min(error_count * 0.03, 0.12)
        confidence = base + evidence - error_penalty

        if not named_tools and not task_context.get("files") and not task_context.get("errors"):
            confidence = min(confidence, 0.35)

        return max(0.0, min(1.0, confidence))

    def _persist_reflection(
        self,
        reflection: ReflectionResult,
        *,
        trace_safety: Any | None = None,
    ) -> None:
        """Save reflection to long-term memory."""
        if (
            self.memory is None
            or not reflection.value_decision.accepted
            or not reflection.claim_validation.valid_claims
        ):
            return

        entry = reflection.to_memory_entry()
        try:
            # Use add_entry if available (new API), fallback to add (old API)
            if hasattr(self.memory, "add_entry"):
                safety_kwargs = {}
                if trace_safety is not None and not trace_safety.allowed:
                    safety_kwargs = {
                        "safety_status": trace_safety.status,
                        "safety_reason": trace_safety.reason,
                    }
                self.memory.add_entry(
                    scope=MemoryScope.PROJECT,
                    category=entry["category"],
                    content=entry["content"],
                    tags=entry["tags"],
                    domains=entry.get("domains", []),
                    metadata=entry.get("metadata", {}),
                    source="reflection",
                    approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
                    **safety_kwargs,
                )
            else:
                if trace_safety is not None and not trace_safety.allowed:
                    logger.warning(
                        "Skipped reflection persistence: legacy memory adapter "
                        "cannot represent safety status %s",
                        trace_safety.status,
                    )
                    return
                self.memory.add(
                    content=entry["content"],
                    scope=MemoryScope.PROJECT,
                    category=entry["category"],
                    tags=entry["tags"],
                    metadata=entry["metadata"],
                )
            logger.info("Reflection persisted to memory (confidence: %.2f)", reflection.confidence)
        except Exception as e:
            logger.warning("Failed to persist reflection: %s", e)
