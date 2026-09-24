"""Post-task Memory contribution review — evidence-cited per-memory adjudication.

Whole-turn feedback cannot attribute utility to individual memories: a task
success would equally reward a memory whose path claim actually failed while
the agent recovered on its own.  This module closes that gap with a reviewer
that runs after a task and reads three inputs the Turn already produces:

- the task description,
- the exact injected memories (``MemoryPromptReceipt.rendered_fragments``
  plus their authority versions),
- the sanitized execution trace (tool calls, tool results, task result).

For each rendered memory it returns one verdict:

===============  ===========================================  ====================
observed evidence conclusion                                     feedback
===============  ===========================================  ====================
path claim read   path valid, memory may have helped            small positive
and succeeded
path claim read   path claim failed in the current environment  corroborated
and failed                                                     negative
no matching tool  contribution cannot be judged                  no score change
event / unclear
================= ============================================ ====================

Two properties are enforced in code, not just in the prompt:

1. **Citation gate.**  An LLM ``helped``/``invalidated`` verdict survives only
   if it quotes text that overlaps the actually rendered fragment and cites
   event IDs that exist in the provided trace (with the required error/success
   polarity).  Anything else is downgraded to ``undetermined`` — task success
   alone is never evidence that a memory helped.
2. **Confidence.**  Every applied verdict is a bounded-confidence inference;
   below ``min_confidence`` no feedback is recorded at all.

The reviewer never raises into task finalization: provider failures fall back
to the deterministic rule verdicts, and feedback application is best-effort
with per-verdict observation receipts for idempotency.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from minicode.agent_budget import AgentBudgetExceeded, record_budgeted_model_call
from minicode.logging_config import get_logger
from minicode.model_call_control import checkpoint_model_call
from minicode.pricing import pricing_failure_event_payload, project_model_cost_event
from minicode.run_events import (
    emit_event_safely,
    new_model_operation_id,
    project_model_duration_ms,
    project_model_usage,
)
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.types import ModelUsage

logger = get_logger("memory_contribution_review")


REVIEW_OUTCOME_HELPED = "helped"
REVIEW_OUTCOME_INVALIDATED = "invalidated"
REVIEW_OUTCOME_UNDETERMINED = "undetermined"
_REVIEW_OUTCOMES = frozenset(
    {REVIEW_OUTCOME_HELPED, REVIEW_OUTCOME_INVALIDATED, REVIEW_OUTCOME_UNDETERMINED}
)
_REVIEW_MODES = frozenset({"off", "rule", "llm"})

# Tokens that look like file paths or file names.  Either they contain a
# separator, or they carry a letter-led extension of 2-8 alphanumeric chars
# (so "settings.py" matches but "v1.2", "e.g" and bare words do not).
_PATH_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+"
    r"|[A-Za-z0-9_][A-Za-z0-9_\-]*\.[A-Za-z][A-Za-z0-9]{1,7}"
)
_MIN_PATH_CLAIM_LEN = 4

_REVIEW_SYSTEM_PROMPT = (
    "You are a memory contribution reviewer. After a coding task you receive: "
    "the task description, the memories that were actually injected into the "
    "agent's prompt, and the sanitized tool-event trace of the run.\n"
    "For EACH memory decide one outcome:\n"
    "- helped: the agent acted on this memory's concrete claim (for example "
    "read the exact path it named) and that action succeeded. This proves the "
    "claim is valid here; it does NOT prove the agent would have failed "
    "without the memory.\n"
    "- invalidated: the agent acted on this memory's concrete claim and the "
    "environment refuted it (for example the named path returned file not "
    "found).\n"
    "- undetermined: the trace shows no corresponding action, or the linkage "
    "is unclear.\n"
    "Hard rules: cite the exact memory text you relied on in memory_quote; "
    "cite the trace event_ids you relied on in tool_event_ids; never judge a "
    "memory helped merely because the task succeeded; give a confidence "
    "between 0 and 1 reflecting that this is an inference, not a proof.\n"
    "Respond with JSON only: {\"verdicts\": [{\"memory_id\": str, \"outcome\": "
    "\"helped|invalidated|undetermined\", \"confidence\": number, "
    "\"memory_quote\": str, \"tool_event_ids\": [str], \"reasoning\": str}]}"
)


def _clamp(value: float, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return min(high, max(low, number))


@dataclass(frozen=True, slots=True)
class ContributionReviewConfig:
    """Bounded knobs for the contribution reviewer. All values are clamped."""

    mode: str = "rule"
    timeout_seconds: float = 15.0
    max_output_tokens: int = 1500
    max_input_bytes: int = 24576
    min_confidence: float = 0.6
    max_memories: int = 8
    max_related_events_per_memory: int = 6

    def __post_init__(self) -> None:
        mode = str(self.mode).strip().lower()
        if mode not in _REVIEW_MODES:
            mode = "rule"
        object.__setattr__(self, "mode", mode)
        object.__setattr__(
            self, "timeout_seconds", _clamp(self.timeout_seconds, 1.0, 60.0, 15.0)
        )
        object.__setattr__(
            self, "max_output_tokens", int(_clamp(self.max_output_tokens, 256, 8192, 1500))
        )
        object.__setattr__(
            self, "max_input_bytes", int(_clamp(self.max_input_bytes, 2048, 131072, 24576))
        )
        object.__setattr__(
            self, "min_confidence", _clamp(self.min_confidence, 0.0, 1.0, 0.6)
        )
        object.__setattr__(
            self, "max_memories", int(_clamp(self.max_memories, 1, 32, 8))
        )
        object.__setattr__(
            self,
            "max_related_events_per_memory",
            int(_clamp(self.max_related_events_per_memory, 1, 32, 6)),
        )

    @classmethod
    def from_runtime(cls, runtime: dict[str, Any] | None = None) -> "ContributionReviewConfig":
        values = dict(runtime or {})
        raw_mode = (
            values.get("memory_contribution_review_mode")
            or os.environ.get("MINI_CODE_MEMORY_CONTRIBUTION_REVIEW")
            or "rule"
        )
        raw_confidence = (
            values.get("memory_contribution_review_min_confidence")
            or os.environ.get(
                "MINI_CODE_MEMORY_CONTRIBUTION_REVIEW_MIN_CONFIDENCE"
            )
            or 0.6
        )
        raw_timeout = (
            values.get("memory_contribution_review_timeout_seconds")
            or os.environ.get("MINI_CODE_MEMORY_CONTRIBUTION_REVIEW_TIMEOUT_SECONDS")
            or 15.0
        )
        return cls(
            mode=str(raw_mode),
            min_confidence=_clamp(raw_confidence, 0.0, 1.0, 0.6),
            timeout_seconds=_clamp(raw_timeout, 1.0, 60.0, 15.0),
        )


@dataclass(frozen=True, slots=True)
class RelatedToolEvent:
    """One tool call/result pair that exercised a memory's path claim."""

    event_id: str
    tool_name: str
    call_summary: str
    result_status: str  # "ok" | "error" | "unknown"
    result_summary: str
    result_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryEvidencePack:
    """A rendered memory plus the trace events deterministically linked to it."""

    memory_id: str
    authority_version: str
    fragment_text: str
    path_claims: tuple[str, ...] = ()
    related_events: tuple[RelatedToolEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryContributionVerdict:
    """Per-memory judgment. Non-``undetermined`` verdicts always carry citations."""

    memory_id: str
    outcome: str
    confidence: float = 0.0
    memory_quote: str = ""
    tool_event_ids: tuple[str, ...] = ()
    reasoning: str = ""
    basis: str = "rule"  # "llm" | "rule"
    downgrade_reason: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryContributionReview:
    """Result of reviewing one prompt receipt against one execution trace."""

    receipt_id: str
    operation_id: str
    task_summary: str
    verdicts: tuple[MemoryContributionVerdict, ...] = ()
    review_source: str = "rule"  # llm | rule | rule_fallback | no_evidence | off
    failure_code: str | None = None
    model_called: bool = False
    latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class ContributionFeedbackApplication:
    """Accepted feedback counts after version fences and idempotency."""

    helped: int = 0
    invalidated: int = 0
    skipped: int = 0


def _bound_text(value: object, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    half = max(1, limit // 2)
    return f"{text[:half]}...{text[-half:]}"


def extract_path_claims(text: str) -> list[str]:
    """Extract file-path-like tokens from a memory fragment.

    Only concrete targets are useful for binding a memory to tool events:
    slash-containing paths and bare file names with a letter-led extension.
    """
    claims: list[str] = []
    seen: set[str] = set()
    for match in _PATH_TOKEN_RE.finditer(str(text or "")):
        token = match.group(0).strip(".,;:()[]{}\"'")
        if len(token) < _MIN_PATH_CLAIM_LEN or token in seen:
            continue
        seen.add(token)
        claims.append(token)
    return claims


@dataclass(frozen=True, slots=True)
class _TraceIndex:
    events_by_id: dict[str, dict]
    calls_by_call_id: dict[str, dict]
    results_by_call_id: dict[str, dict]
    event_error: dict[str, bool | None]


def _index_trace(execution_trace: list[dict] | None) -> _TraceIndex:
    events_by_id: dict[str, dict] = {}
    calls_by_call_id: dict[str, dict] = {}
    results_by_call_id: dict[str, dict] = {}
    for event in execution_trace or []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id", "")).strip()
        if event_id:
            events_by_id[event_id] = event
        call_id = str(event.get("call_id", "")).strip()
        if not call_id:
            continue
        if event.get("type") == "tool_call":
            calls_by_call_id.setdefault(call_id, event)
        elif event.get("type") == "tool_result":
            results_by_call_id.setdefault(call_id, event)
    event_error: dict[str, bool | None] = {}
    for event_id, event in events_by_id.items():
        if event.get("type") == "tool_result":
            event_error[event_id] = bool(event.get("is_error"))
        elif event.get("type") == "tool_call":
            result = results_by_call_id.get(str(event.get("call_id", "")))
            event_error[event_id] = (
                bool(result.get("is_error")) if result is not None else None
            )
    return _TraceIndex(events_by_id, calls_by_call_id, results_by_call_id, event_error)


def _related_event_for(
    call_event: dict,
    index: _TraceIndex,
    *,
    summary_limit: int,
) -> RelatedToolEvent:
    call_id = str(call_event.get("call_id", ""))
    result = index.results_by_call_id.get(call_id)
    if result is None:
        status = "unknown"
        result_summary = ""
        result_event_ids: tuple[str, ...] = ()
    else:
        is_error = bool(result.get("is_error"))
        status = "error" if is_error else "ok"
        result_summary = _bound_text(result.get("output_summary"), summary_limit)
        result_event_ids = (
            (str(result.get("event_id", "")).strip(),)
            if str(result.get("event_id", "")).strip()
            else ()
        )
    try:
        call_summary = _bound_text(
            json.dumps(call_event.get("input", {}), ensure_ascii=False, sort_keys=True),
            summary_limit,
        )
    except (TypeError, ValueError):
        call_summary = _bound_text(call_event.get("input"), summary_limit)
    return RelatedToolEvent(
        event_id=str(call_event.get("event_id", "")).strip(),
        tool_name=str(call_event.get("tool_name", "")),
        call_summary=call_summary,
        result_status=status,
        result_summary=result_summary,
        result_event_ids=result_event_ids,
    )


def build_evidence_packs(
    receipt: Any,
    execution_trace: list[dict] | None,
    *,
    max_memories: int = 8,
    max_related_events: int = 6,
) -> list[MemoryEvidencePack]:
    """Bind each rendered memory fragment to trace events touching its claims.

    The binding is deterministic substring matching of path claims against
    serialized tool-call inputs, so it doubles as the rule-mode verdict source
    and as the LLM's pre-digested evidence.
    """
    fragments = tuple(getattr(receipt, "rendered_fragments", ()) or ())
    if not fragments:
        return []
    versions = dict(getattr(receipt, "rendered_versions", ()) or {})
    index = _index_trace(execution_trace)
    packs: list[MemoryEvidencePack] = []
    for entry_id, digest, fragment_text in fragments[:max_memories]:
        fragment = str(fragment_text or "")
        claims = tuple(extract_path_claims(fragment))
        related: list[RelatedToolEvent] = []
        if claims:
            for call_event in index.calls_by_call_id.values():
                if len(related) >= max_related_events:
                    break
                try:
                    serialized = json.dumps(
                        call_event.get("input", {}),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                except (TypeError, ValueError):
                    serialized = str(call_event.get("input", ""))
                files = " ".join(
                    str(item) for item in (call_event.get("files") or [])
                )
                if any(claim in serialized or claim in files for claim in claims):
                    related.append(
                        _related_event_for(call_event, index, summary_limit=200)
                    )
        packs.append(
            MemoryEvidencePack(
                memory_id=str(entry_id),
                authority_version=str(versions.get(str(entry_id), "")),
                fragment_text=fragment,
                path_claims=claims,
                related_events=tuple(related),
            )
        )
    return packs


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _quote_matches_fragment(quote: str, fragment: str) -> bool:
    """Meaningful overlap between a cited quote and the rendered fragment."""
    normalized_quote = _normalize_for_match(quote)
    normalized_fragment = _normalize_for_match(fragment)
    if not normalized_quote or not normalized_fragment:
        return False
    if len(normalized_quote) >= 8 and normalized_quote in normalized_fragment:
        return True
    if len(normalized_fragment) >= 8 and normalized_fragment in normalized_quote:
        return True
    quote_tokens = {
        token
        for token in re.findall(r"[A-Za-z0-9_.\-/]{3,}", normalized_quote)
    }
    fragment_tokens = {
        token
        for token in re.findall(r"[A-Za-z0-9_.\-/]{3,}", normalized_fragment)
    }
    if not quote_tokens:
        return False
    overlap = len(quote_tokens & fragment_tokens) / len(quote_tokens)
    return overlap >= 0.6


class MemoryContributionReviewer:
    """Adjudicate whether each injected memory contributed to the task."""

    def __init__(
        self,
        config: ContributionReviewConfig | None = None,
        *,
        client: Any = None,
        model_adapter: Any = None,
    ) -> None:
        self._config = config if config is not None else ContributionReviewConfig()
        self._client = client
        if self._client is None and model_adapter is not None:
            from minicode.reflection_llm import ModelAdapterStructuredGenerationClient

            self._client = ModelAdapterStructuredGenerationClient(model_adapter)

    @property
    def config(self) -> ContributionReviewConfig:
        return self._config

    # ── public entrypoint ──────────────────────────────────────────

    def review(
        self,
        task_description: str,
        receipt: Any,
        execution_trace: list[dict] | None,
        *,
        operation_id: str | None = None,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> MemoryContributionReview:
        started = time.monotonic()
        task_summary = _bound_text(task_description, 400)
        effective_operation_id = str(operation_id).strip() or new_model_operation_id()
        receipt_id = str(getattr(receipt, "receipt_id", "") or "")

        def _finish(
            verdicts: tuple[MemoryContributionVerdict, ...],
            source: str,
            *,
            failure_code: str | None = None,
            model_called: bool = False,
        ) -> MemoryContributionReview:
            return MemoryContributionReview(
                receipt_id=receipt_id,
                operation_id=effective_operation_id,
                task_summary=task_summary,
                verdicts=verdicts,
                review_source=source,
                failure_code=failure_code,
                model_called=model_called,
                latency_ms=max(0.0, (time.monotonic() - started) * 1000),
            )

        if self._config.mode == "off":
            return _finish((), "off")

        packs = build_evidence_packs(
            receipt,
            execution_trace,
            max_memories=self._config.max_memories,
            max_related_events=self._config.max_related_events_per_memory,
        )
        if not packs:
            return _finish((), "no_evidence")

        if self._config.mode != "llm" or self._client is None:
            failure_code = (
                "review_model_not_configured"
                if self._config.mode == "llm"
                else None
            )
            return _finish(
                self._rule_verdicts(packs),
                "rule_fallback" if failure_code else "rule",
                failure_code=failure_code,
            )

        index = _index_trace(execution_trace)
        llm_outcome = self._run_llm_review(
            task_summary,
            packs,
            index,
            effective_operation_id,
            agent_budget=agent_budget,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )
        if llm_outcome.verdicts is None:
            return _finish(
                self._rule_verdicts(packs),
                "rule_fallback",
                failure_code=llm_outcome.failure_code,
                model_called=True,
            )
        verdicts = self._merge_llm_verdicts(llm_outcome.verdicts, packs, index)
        return _finish(verdicts, "llm", model_called=True)

    # ── rule mode ──────────────────────────────────────────────────

    def _rule_verdicts(
        self, packs: list[MemoryEvidencePack]
    ) -> tuple[MemoryContributionVerdict, ...]:
        verdicts: list[MemoryContributionVerdict] = []
        for pack in packs:
            quote = pack.path_claims[0] if pack.path_claims else ""
            failing = [
                event for event in pack.related_events if event.result_status == "error"
            ]
            succeeding = [
                event for event in pack.related_events if event.result_status == "ok"
            ]
            if (
                failing
                and succeeding
                and len(pack.path_claims) > 1
            ):
                # A recovery lesson names both a documented failure and the
                # correct target. When the trace exercises both, the
                # deterministic matcher cannot tell which claim each outcome
                # belongs to: punishing the lesson for its own documented
                # failure would be a false invalidation. Ambiguity gets no
                # verdict; the LLM reviewer adjudicates this nuance.
                verdicts.append(
                    MemoryContributionVerdict(
                        memory_id=pack.memory_id,
                        outcome=REVIEW_OUTCOME_UNDETERMINED,
                        confidence=0.0,
                        memory_quote=quote,
                        tool_event_ids=(),
                        reasoning=(
                            "multiple path claims match trace events with "
                            "both success and failure polarity; rule review "
                            "cannot attribute outcomes to the right claim"
                        ),
                        basis="rule",
                    )
                )
                continue
            if failing:
                event = failing[0]
                verdicts.append(
                    MemoryContributionVerdict(
                        memory_id=pack.memory_id,
                        outcome=REVIEW_OUTCOME_INVALIDATED,
                        confidence=0.9,
                        memory_quote=quote,
                        tool_event_ids=(event.event_id, *event.result_event_ids),
                        reasoning=(
                            f"path claim {quote!r} was exercised by tool "
                            f"{event.tool_name!r} and failed in this environment"
                        ),
                        basis="rule",
                    )
                )
            elif succeeding:
                event = succeeding[0]
                verdicts.append(
                    MemoryContributionVerdict(
                        memory_id=pack.memory_id,
                        outcome=REVIEW_OUTCOME_HELPED,
                        confidence=0.7,
                        memory_quote=quote,
                        tool_event_ids=(event.event_id, *event.result_event_ids),
                        reasoning=(
                            f"path claim {quote!r} was exercised by tool "
                            f"{event.tool_name!r} and succeeded; the memory may "
                            "have helped"
                        ),
                        basis="rule",
                    )
                )
            else:
                verdicts.append(
                    MemoryContributionVerdict(
                        memory_id=pack.memory_id,
                        outcome=REVIEW_OUTCOME_UNDETERMINED,
                        confidence=0.0,
                        memory_quote="",
                        tool_event_ids=(),
                        reasoning="no trace event exercised this memory's claims",
                        basis="rule",
                    )
                )
        return tuple(verdicts)

    # ── LLM mode ───────────────────────────────────────────────────

    def _run_llm_review(
        self,
        task_summary: str,
        packs: list[MemoryEvidencePack],
        index: _TraceIndex,
        operation_id: str,
        *,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> "_LLMReviewOutcome":
        budget_reservation = None
        started = time.monotonic()
        model_operation_id = new_model_operation_id()
        model_identity = str(getattr(self._client, "model_id", "") or "")
        try:
            checkpoint_model_call(
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            messages = self._build_messages(task_summary, packs, index)
            encoded_size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
            if agent_budget is not None:
                try:
                    budget_reservation = agent_budget.reserve_model_call(
                        max(1, encoded_size // 4),
                        preserve_model_calls=1,
                    )
                except AgentBudgetExceeded:
                    return _LLMReviewOutcome(
                        None, failure_code="agent_budget_exhausted"
                    )
            emit_event_safely(
                event_sink,
                "model.started",
                payload={
                    "operationId": model_operation_id,
                    "purpose": "memory_contribution_review",
                },
            )
            response = self._client.generate_json(
                messages,
                timeout_seconds=self._config.timeout_seconds,
                max_output_tokens=self._config.max_output_tokens,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            if getattr(response, "tool_calls", None):
                return self._fail_llm(
                    event_sink,
                    agent_budget,
                    budget_reservation,
                    model_operation_id,
                    started,
                    "tool_call_rejected",
                )
            stop_reason = getattr(response, "stop_reason", None)
            if isinstance(stop_reason, str) and stop_reason.lower() in {
                "length",
                "max_tokens",
            }:
                return self._fail_llm(
                    event_sink,
                    agent_budget,
                    budget_reservation,
                    model_operation_id,
                    started,
                    "output_limit_reached",
                )
            text = str(getattr(response, "text", "") or "")
            payload = self._parse_json_verdicts(text)
            if payload is None:
                return self._fail_llm(
                    event_sink,
                    agent_budget,
                    budget_reservation,
                    model_operation_id,
                    started,
                    "invalid_output_json",
                )
            usage = ModelUsage(
                input_tokens=int(
                    getattr(response, "input_tokens", None)
                    or max(1, encoded_size // 8)
                ),
                output_tokens=int(getattr(response, "output_tokens", None) or 1),
                source="estimated",
            )
            usage_payload = project_model_usage(usage)
            try:
                cost_payload = project_model_cost_event(
                    model=model_identity,
                    usage=usage_payload,
                    operation_id=model_operation_id,
                )
            except BaseException:  # noqa: BLE001 - observation stays optional
                cost_payload = pricing_failure_event_payload(model_operation_id)
            record_budgeted_model_call(
                agent_budget,
                model=model_identity,
                usage=usage_payload,
                reservation=budget_reservation,
                cost_payload=cost_payload,
            )
            completed: dict[str, object] = {
                "operationId": model_operation_id,
                "purpose": "memory_contribution_review",
                "resultType": "assistant",
                "contentPresent": bool(text),
                "toolCallCount": 0,
                "usage": usage_payload,
            }
            duration_ms = project_model_duration_ms(started, time.monotonic())
            if duration_ms is not None:
                completed["durationMs"] = duration_ms
            emit_event_safely(event_sink, "model.completed", payload=completed)
            cost_payload["purpose"] = "memory_contribution_review"
            emit_event_safely(event_sink, "model.costed", payload=cost_payload)
            return _LLMReviewOutcome(payload)
        except TurnCancellationRequested:
            self._release_budget(event_sink, agent_budget, budget_reservation)
            raise
        except TimeoutError:
            return self._fail_llm(
                event_sink,
                agent_budget,
                budget_reservation,
                model_operation_id,
                started,
                "provider_timeout",
            )
        except AgentBudgetExceeded:
            return self._fail_llm(
                event_sink,
                agent_budget,
                budget_reservation,
                model_operation_id,
                started,
                "agent_budget_exhausted",
            )
        except Exception:  # noqa: BLE001 - review degrades, never blocks
            logger.warning("Memory contribution LLM review failed", exc_info=True)
            return self._fail_llm(
                event_sink,
                agent_budget,
                budget_reservation,
                model_operation_id,
                started,
                "provider_error",
            )

    def _fail_llm(
        self,
        event_sink: Any,
        agent_budget: Any,
        budget_reservation: Any,
        model_operation_id: str,
        started: float,
        failure_code: str,
    ) -> "_LLMReviewOutcome":
        try:
            emit_event_safely(
                event_sink,
                "model.failed",
                payload={
                    "operationId": model_operation_id,
                    "purpose": "memory_contribution_review",
                    "errorCode": failure_code,
                },
            )
        except Exception:  # noqa: BLE001
            pass
        self._release_budget(event_sink, agent_budget, budget_reservation)
        logger.info(
            "Memory contribution review fell back to rule verdicts: %s",
            failure_code,
        )
        return _LLMReviewOutcome(None, failure_code=failure_code)

    def _release_budget(
        self,
        event_sink: Any,
        agent_budget: Any,
        budget_reservation: Any,
    ) -> None:
        if budget_reservation is None:
            return
        try:
            release = getattr(agent_budget, "release_model_call", None)
            if callable(release):
                release(budget_reservation)
        except Exception:  # noqa: BLE001
            logger.debug("Failed to release review budget reservation", exc_info=True)

    def _build_messages(
        self,
        task_summary: str,
        packs: list[MemoryEvidencePack],
        index: _TraceIndex,
    ) -> list[dict[str, str]]:
        memory_payload = [
            {
                "memory_id": pack.memory_id,
                "rendered_fragment": pack.fragment_text,
                "related_events": [
                    {
                        "event_id": event.event_id,
                        "tool": event.tool_name,
                        "call": event.call_summary,
                        "result_status": event.result_status,
                        "result": event.result_summary,
                    }
                    for event in pack.related_events
                ],
            }
            for pack in packs
        ]
        tool_events_payload = []
        for event_id, event in list(index.events_by_id.items())[:80]:
            entry: dict[str, object] = {
                "event_id": event_id,
                "type": event.get("type"),
            }
            if event.get("tool_name"):
                entry["tool"] = event.get("tool_name")
            if event.get("type") == "tool_call":
                entry["input"] = _bound_text(
                    json.dumps(
                        event.get("input", {}),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    120,
                )
            elif event.get("type") == "tool_result":
                entry["status"] = event.get("status")
                entry["output"] = _bound_text(event.get("output_summary"), 120)
            elif event.get("type") == "task_result":
                entry["status"] = event.get("status")
                entry["summary"] = _bound_text(event.get("summary"), 160)
            tool_events_payload.append(entry)
        user_payload = {
            "task": task_summary,
            "memories": memory_payload,
            "tool_events": tool_events_payload,
        }
        serialized = json.dumps(user_payload, ensure_ascii=False)
        budget = self._config.max_input_bytes
        if len(serialized.encode("utf-8")) > budget:
            # Trim the free-form event index first; the per-memory related
            # events are the evidence the citation gate relies on.
            tool_events_payload = tool_events_payload[:20]
            user_payload["tool_events"] = tool_events_payload
            serialized = json.dumps(user_payload, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > budget:
            for memory in memory_payload:
                memory["rendered_fragment"] = _bound_text(
                    memory["rendered_fragment"], 400
                )
            serialized = json.dumps(user_payload, ensure_ascii=False)
        return [
            {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": serialized},
        ]

    @staticmethod
    def _parse_json_verdicts(text: str) -> list[dict] | None:
        stripped = str(text or "").strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        try:
            payload = json.loads(stripped)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        verdicts = payload.get("verdicts")
        if not isinstance(verdicts, list):
            return None
        return [item for item in verdicts if isinstance(item, dict)]

    def _merge_llm_verdicts(
        self,
        raw_verdicts: list[dict],
        packs: list[MemoryEvidencePack],
        index: _TraceIndex,
    ) -> tuple[MemoryContributionVerdict, ...]:
        packs_by_id = {pack.memory_id: pack for pack in packs}
        merged: dict[str, MemoryContributionVerdict] = {}
        for raw in raw_verdicts:
            memory_id = str(raw.get("memory_id", "")).strip()
            pack = packs_by_id.get(memory_id)
            if pack is None or memory_id in merged:
                continue
            merged[memory_id] = self._validated_verdict(raw, pack, index)
        verdicts: list[MemoryContributionVerdict] = []
        for pack in packs:
            verdict = merged.get(pack.memory_id)
            if verdict is None:
                verdict = MemoryContributionVerdict(
                    memory_id=pack.memory_id,
                    outcome=REVIEW_OUTCOME_UNDETERMINED,
                    confidence=0.0,
                    memory_quote="",
                    tool_event_ids=(),
                    reasoning="not adjudicated by the reviewer",
                    basis="llm",
                )
            verdicts.append(verdict)
        return tuple(verdicts)

    def _validated_verdict(
        self,
        raw: dict,
        pack: MemoryEvidencePack,
        index: _TraceIndex,
    ) -> MemoryContributionVerdict:
        memory_id = pack.memory_id
        outcome = str(raw.get("outcome", "")).strip().lower()
        if outcome not in _REVIEW_OUTCOMES:
            outcome = REVIEW_OUTCOME_UNDETERMINED
        confidence = _clamp(raw.get("confidence"), 0.0, 1.0, 0.0)
        quote = _bound_text(raw.get("memory_quote"), 400)
        reasoning = _bound_text(raw.get("reasoning"), 400)
        raw_event_ids = raw.get("tool_event_ids")
        event_ids = tuple(
            str(item).strip()
            for item in (raw_event_ids if isinstance(raw_event_ids, list) else [])
            if str(item).strip()
        )

        def _downgraded(reason: str) -> MemoryContributionVerdict:
            return MemoryContributionVerdict(
                memory_id=memory_id,
                outcome=REVIEW_OUTCOME_UNDETERMINED,
                confidence=confidence,
                memory_quote=quote,
                tool_event_ids=event_ids,
                reasoning=reasoning,
                basis="llm",
                downgrade_reason=reason,
            )

        if outcome == REVIEW_OUTCOME_UNDETERMINED:
            return MemoryContributionVerdict(
                memory_id=memory_id,
                outcome=outcome,
                confidence=confidence,
                memory_quote=quote,
                tool_event_ids=event_ids,
                reasoning=reasoning,
                basis="llm",
            )
        if confidence < self._config.min_confidence:
            return _downgraded(
                f"confidence {confidence:.2f} below threshold "
                f"{self._config.min_confidence:.2f}"
            )
        if not quote or not _quote_matches_fragment(quote, pack.fragment_text):
            return _downgraded("memory_quote does not match the rendered fragment")
        if not event_ids:
            return _downgraded("no tool events cited")
        known_error_states: list[bool | None] = []
        for event_id in event_ids:
            if event_id not in index.events_by_id:
                return _downgraded(f"cited event {event_id} not present in trace")
            known_error_states.append(index.event_error.get(event_id))
        if outcome == REVIEW_OUTCOME_INVALIDATED and not any(
            state is True for state in known_error_states
        ):
            return _downgraded("invalidated requires a cited failing tool event")
        if outcome == REVIEW_OUTCOME_HELPED and not any(
            state is False for state in known_error_states
        ):
            return _downgraded("helped requires a cited successful tool event")
        return MemoryContributionVerdict(
            memory_id=memory_id,
            outcome=outcome,
            confidence=confidence,
            memory_quote=quote,
            tool_event_ids=event_ids,
            reasoning=reasoning,
            basis="llm",
        )


@dataclass(frozen=True, slots=True)
class _LLMReviewOutcome:
    verdicts: list[dict] | None
    failure_code: str | None = None


# ── feedback application ───────────────────────────────────────────


def _entry_feedback_snapshot(
    memory_manager: Any, entry_ids: list[str]
) -> dict[str, tuple[int, int, int]]:
    snapshot: dict[str, tuple[int, int, int]] = {}
    memories = getattr(memory_manager, "memories", None)
    if not isinstance(memories, dict):
        return snapshot
    for scope_file in memories.values():
        for entry in getattr(scope_file, "entries", ()) or ():
            entry_id = getattr(entry, "id", None)
            if entry_id in entry_ids and entry_id not in snapshot:
                snapshot[entry_id] = (
                    int(getattr(entry, "success_count", 0) or 0),
                    int(getattr(entry, "failure_count", 0) or 0),
                    int(getattr(entry, "corroborated_failure_count", 0) or 0),
                )
    return snapshot


def apply_contribution_feedback(
    memory_manager: Any,
    review: MemoryContributionReview,
    receipt: Any,
) -> ContributionFeedbackApplication:
    """Record per-verdict feedback, fenced to the receipt's memory versions.

    ``helped`` uses the ordinary channel with a dedicated source (small
    positive: one success count); ``invalidated`` uses the corroborated
    independent-verification channel, which is the existing correction flow
    (two independent failures quarantine the entry).  ``undetermined`` never
    touches accounting.  Observation receipts keyed by operation + memory +
    channel make replays idempotent.
    """
    versions = {
        str(entry_id): str(digest)
        for entry_id, digest in (getattr(receipt, "rendered_versions", ()) or ())
    }
    helped_ids = [
        verdict.memory_id
        for verdict in review.verdicts
        if verdict.outcome == REVIEW_OUTCOME_HELPED and verdict.memory_id in versions
    ]
    invalidated_ids = [
        verdict.memory_id
        for verdict in review.verdicts
        if verdict.outcome == REVIEW_OUTCOME_INVALIDATED
        and verdict.memory_id in versions
    ]
    operation_id = review.operation_id
    before = _entry_feedback_snapshot(
        memory_manager, helped_ids + invalidated_ids
    )
    for entry_id in helped_ids:
        try:
            memory_manager.record_feedback(
                [entry_id],
                True,
                source="contribution_review",
                observation_id=f"{operation_id}:positive:{entry_id}",
                expected_versions=[(entry_id, versions[entry_id])],
            )
        except Exception:  # noqa: BLE001 - feedback stays best-effort
            logger.warning(
                "Contribution review positive feedback failed for %s",
                entry_id,
                exc_info=True,
            )
    for entry_id in invalidated_ids:
        try:
            memory_manager.record_corroborated_feedback(
                [entry_id],
                False,
                source="independent_verification",
                observation_id=f"{operation_id}:verification:{entry_id}",
                expected_versions=[(entry_id, versions[entry_id])],
            )
        except Exception:  # noqa: BLE001 - feedback stays best-effort
            logger.warning(
                "Contribution review verification feedback failed for %s",
                entry_id,
                exc_info=True,
            )
    after = _entry_feedback_snapshot(
        memory_manager, helped_ids + invalidated_ids
    )
    helped = sum(
        1
        for entry_id in helped_ids
        if after.get(entry_id, (0, 0, 0))[0] > before.get(entry_id, (0, 0, 0))[0]
    )
    invalidated = sum(
        1
        for entry_id in invalidated_ids
        if after.get(entry_id, (0, 0, 0))[2] > before.get(entry_id, (0, 0, 0))[2]
    )
    return ContributionFeedbackApplication(
        helped=helped,
        invalidated=invalidated,
        skipped=len(helped_ids) + len(invalidated_ids) - helped - invalidated,
    )
