"""Memory contribution reviewer: evidence-cited post-task adjudication.

The reviewer closes the gap between what Memory actually injected (prompt
receipt) and what the agent actually did (execution trace).  Whole-turn
feedback cannot attribute utility per memory; these tests pin the per-memory
contract:

- ``helped``      path claim read successfully     -> small positive feedback
- ``invalidated`` path claim failed in environment -> corroborated negative
- ``undetermined`` no observable linkage            -> no score change

Every ``helped``/``invalidated`` verdict must cite a real memory quote and
real tool events; task success alone is never evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import minicode.memory as memory_module
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_contribution_reviewer import (
    ContributionReviewConfig,
    MemoryContributionReviewer,
    MemoryContributionReview,
    MemoryContributionVerdict,
    apply_contribution_feedback,
    build_evidence_packs,
    extract_path_claims,
)
from minicode.memory_pipeline import MemoryPromptReceipt, MemoryPipeline
from minicode.reflection_evidence import append_trace_event
from minicode.reflection_llm import StructuredGenerationResponse


# ── fixtures ───────────────────────────────────────────────────────


def _manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryManager:
    monkeypatch.setattr(
        memory_module,
        "MINI_CODE_DIR",
        tmp_path / "home" / ".mini-code",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return MemoryManager(project_root=workspace)


def _add_entry(manager: MemoryManager, content: str) -> tuple[str, str]:
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "lesson",
        content,
        tags=["pytest"],
        source="test",
    )
    assert entry is not None
    return entry.id, entry.approval_content_hash


def _receipt(
    fragments: list[tuple[str, str, str]],
    *,
    receipt_id: str = "receipt-1",
) -> MemoryPromptReceipt:
    return MemoryPromptReceipt(
        prompt_text="\n".join(fragment for _, _, fragment in fragments),
        rendered_ids=tuple(entry_id for entry_id, _, _ in fragments),
        rendered_versions=tuple(
            (entry_id, version) for entry_id, version, _ in fragments
        ),
        authority_revision="rev-1",
        receipt_id=receipt_id,
        rendered_proofs=tuple(
            (entry_id, f"proof-{entry_id}") for entry_id, _, _ in fragments
        ),
        rendered_fragments=tuple(fragments),
    )


def _trace(events: list[dict]) -> list[dict]:
    trace: list[dict] = []
    for event in events:
        append_trace_event(trace, event)
    return trace


def _call(step: int, call_id: str, tool: str, payload: dict) -> dict:
    return {
        "type": "tool_call",
        "step": step,
        "call_id": call_id,
        "tool_name": tool,
        "input": payload,
    }


def _result(step: int, call_id: str, tool: str, ok: bool, summary: str) -> dict:
    return {
        "type": "tool_result",
        "step": step,
        "call_id": call_id,
        "tool_name": tool,
        "status": "success" if ok else "error",
        "is_error": not ok,
        "output_summary": summary,
    }


class _FakeClient:
    """Duck-typed StructuredGenerationClient for review-path tests."""

    def __init__(
        self,
        payload: dict | None = None,
        *,
        text: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.calls: list[list[dict]] = []
        self._text = (
            text if text is not None else json.dumps(payload or {})
        )
        self._error = error

    def generate_json(
        self,
        messages: list[dict],
        *,
        timeout_seconds: float,
        max_output_tokens: int,
        cancellation_token: object | None = None,
        deadline_monotonic: float | None = None,
    ) -> StructuredGenerationResponse:
        self.calls.append(messages)
        if self._error is not None:
            raise self._error
        return StructuredGenerationResponse(text=self._text)


def _verdict(
    memory_id: str,
    outcome: str,
    *,
    confidence: float = 0.9,
    quote: str = "config/settings.py",
    events: tuple[str, ...] = ("event-000001",),
    basis: str = "llm",
) -> MemoryContributionVerdict:
    return MemoryContributionVerdict(
        memory_id=memory_id,
        outcome=outcome,
        confidence=confidence,
        memory_quote=quote,
        tool_event_ids=events,
        reasoning="test verdict",
        basis=basis,
    )


# ── deterministic evidence binding ────────────────────────────────


def test_extract_path_claims_finds_file_like_tokens() -> None:
    text = (
        "Run tests via tests/test_auth.py; config lives in "
        "config/settings.py. The word pytest alone is not a path."
    )
    claims = extract_path_claims(text)
    assert "tests/test_auth.py" in claims
    assert "config/settings.py" in claims
    assert "pytest" not in claims


def test_build_evidence_packs_pairs_memory_paths_with_tool_events() -> None:
    receipt = _receipt(
        [("m1", "v1", "Config lives at config/settings.py in this repo")]
    )
    trace = _trace(
        [
            _call(1, "c1", "read", {"path": "config/settings.py"}),
            _result(1, "c1", "read", True, "loaded 30 lines"),
            _call(2, "c2", "read", {"path": "missing/other.txt"}),
            _result(2, "c2", "read", False, "File not found: missing/other.txt"),
        ]
    )
    packs = build_evidence_packs(receipt, trace)
    assert len(packs) == 1
    pack = packs[0]
    assert pack.memory_id == "m1"
    assert pack.authority_version == "v1"
    assert "config/settings.py" in pack.fragment_text
    assert [event.event_id for event in pack.related_events] == [
        trace[0]["event_id"]
    ]
    assert pack.related_events[0].result_status == "ok"


def test_build_evidence_packs_without_linkage_yields_no_events() -> None:
    receipt = _receipt([("m1", "v1", "Prefer integration tests over mocks")])
    trace = _trace(
        [
            _call(1, "c1", "read", {"path": "config/settings.py"}),
            _result(1, "c1", "read", True, "loaded 30 lines"),
        ]
    )
    packs = build_evidence_packs(receipt, trace)
    assert packs[0].related_events == ()


# ── rule-mode review ───────────────────────────────────────────────


def _rule_reviewer() -> MemoryContributionReviewer:
    return MemoryContributionReviewer(
        ContributionReviewConfig(mode="rule"),
    )


def test_rule_review_marks_successful_path_claim_helped() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    trace = _trace(
        [
            _call(1, "c1", "read", {"path": "config/settings.py"}),
            _result(1, "c1", "read", True, "loaded 30 lines"),
        ]
    )
    review = _rule_reviewer().review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=trace,
        operation_id="op-1",
    )
    assert review.review_source == "rule"
    assert len(review.verdicts) == 1
    verdict = review.verdicts[0]
    assert verdict.outcome == "helped"
    assert verdict.basis == "rule"
    assert verdict.memory_quote
    # The evidence citation covers the call event and its paired result.
    assert verdict.tool_event_ids == (
        trace[0]["event_id"],
        trace[1]["event_id"],
    )


def test_rule_review_keeps_recovery_lesson_undetermined_when_both_polarities_match():
    """A recovery lesson documents BOTH a failing target and the correct one.

    When the trace exercises both (the documented failure fails as
    documented, the correct target succeeds), the deterministic matcher
    cannot attribute polarity to the right claim: punishing the memory for
    its own documented failure would be a false invalidation, and crediting
    help without clean attribution would overreach.  Undetermined is the
    honest verdict; the LLM reviewer exists for exactly this nuance.
    """
    receipt = _receipt(
        [
            (
                "m1",
                "v1",
                "python -m unittest tests.test_config fails; use "
                "python -m unittest ledgerlib.tests.test_config instead",
            )
        ]
    )
    trace = _trace(
        [
            _call(1, "c-wrong", "run_command", {"command": "python", "args": ["-m", "unittest", "tests.test_config"]}),
            _result(1, "c-wrong", "run_command", False, "No module named 'tests.test_config'"),
            _call(2, "c-right", "run_command", {"command": "python", "args": ["-m", "unittest", "ledgerlib.tests.test_config"]}),
            _result(2, "c-right", "run_command", True, "VERIFY-LEDGER-642"),
        ]
    )
    review = _rule_reviewer().review(
        task_description="run the verification suite",
        receipt=receipt,
        execution_trace=trace,
        operation_id="op-1",
    )
    verdict = review.verdicts[0]
    assert verdict.outcome == "undetermined"
    assert "both" in verdict.reasoning or "polarity" in verdict.reasoning


def test_rule_review_marks_failed_path_claim_invalidated() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    trace = _trace(
        [
            _call(1, "c1", "read", {"path": "config/settings.py"}),
            _result(1, "c1", "read", False, "File not found: config/settings.py"),
        ]
    )
    review = _rule_reviewer().review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=trace,
        operation_id="op-1",
    )
    verdict = review.verdicts[0]
    assert verdict.outcome == "invalidated"
    assert verdict.tool_event_ids


def test_rule_review_keeps_unrelated_memory_undetermined() -> None:
    receipt = _receipt([("m1", "v1", "Prefer integration tests over mocks")])
    trace = _trace(
        [
            _call(1, "c1", "read", {"path": "config/settings.py"}),
            _result(1, "c1", "read", True, "loaded 30 lines"),
        ]
    )
    review = _rule_reviewer().review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=trace,
        operation_id="op-1",
    )
    verdict = review.verdicts[0]
    assert verdict.outcome == "undetermined"
    assert verdict.tool_event_ids == ()


# ── LLM review + citation gate ─────────────────────────────────────


def _llm_reviewer(client: _FakeClient) -> MemoryContributionReviewer:
    return MemoryContributionReviewer(
        ContributionReviewConfig(mode="llm"),
        client=client,
    )


def _success_trace() -> list[dict]:
    return _trace(
        [
            _call(1, "c1", "read", {"path": "config/settings.py"}),
            _result(1, "c1", "read", True, "loaded 30 lines"),
        ]
    )


def _failure_trace() -> list[dict]:
    return _trace(
        [
            _call(1, "c1", "read", {"path": "config/settings.py"}),
            _result(1, "c1", "read", False, "File not found: config/settings.py"),
        ]
    )


def test_llm_review_keeps_well_cited_verdict() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    trace = _success_trace()
    client = _FakeClient(
        {
            "verdicts": [
                {
                    "memory_id": "m1",
                    "outcome": "helped",
                    "confidence": 0.85,
                    "memory_quote": "Config lives at config/settings.py",
                    "tool_event_ids": [trace[0]["event_id"]],
                    "reasoning": "agent read the exact path and it loaded",
                }
            ]
        }
    )
    review = _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=trace,
        operation_id="op-1",
    )
    assert review.review_source == "llm"
    assert review.model_called is True
    verdict = review.verdicts[0]
    assert verdict.outcome == "helped"
    assert verdict.downgrade_reason is None


def test_llm_prompt_contains_memory_fragments_and_event_ids() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    trace = _success_trace()
    client = _FakeClient({"verdicts": []})
    _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=trace,
        operation_id="op-1",
    )
    assert len(client.calls) == 1
    blob = json.dumps(client.calls[0])
    assert "config/settings.py" in blob
    assert trace[0]["event_id"] in blob


def test_llm_verdict_citing_unknown_event_is_downgraded() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    client = _FakeClient(
        {
            "verdicts": [
                {
                    "memory_id": "m1",
                    "outcome": "helped",
                    "confidence": 0.9,
                    "memory_quote": "Config lives at config/settings.py",
                    "tool_event_ids": ["event-999999"],
                    "reasoning": "hallucinated event",
                }
            ]
        }
    )
    review = _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=_success_trace(),
        operation_id="op-1",
    )
    assert review.verdicts[0].outcome == "undetermined"
    assert review.verdicts[0].downgrade_reason


def test_llm_verdict_with_unmatched_quote_is_downgraded() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    client = _FakeClient(
        {
            "verdicts": [
                {
                    "memory_id": "m1",
                    "outcome": "helped",
                    "confidence": 0.9,
                    "memory_quote": "totally different content never rendered",
                    "tool_event_ids": ["event-000001"],
                    "reasoning": "quote mismatch",
                }
            ]
        }
    )
    review = _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=_success_trace(),
        operation_id="op-1",
    )
    assert review.verdicts[0].outcome == "undetermined"


def test_llm_helped_without_tool_events_is_downgraded() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    client = _FakeClient(
        {
            "verdicts": [
                {
                    "memory_id": "m1",
                    "outcome": "helped",
                    "confidence": 0.9,
                    "memory_quote": "Config lives at config/settings.py",
                    "tool_event_ids": [],
                    "reasoning": "task succeeded so memory helped",
                }
            ]
        }
    )
    review = _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=_success_trace(),
        operation_id="op-1",
    )
    assert review.verdicts[0].outcome == "undetermined"
    assert review.verdicts[0].downgrade_reason


def test_llm_task_success_alone_cannot_mark_unrelated_memory_helped() -> None:
    receipt = _receipt(
        [
            ("m1", "v1", "Config lives at config/settings.py"),
            ("m2", "v2", "Prefer integration tests over mocks"),
        ]
    )
    trace = _success_trace()
    client = _FakeClient(
        {
            "verdicts": [
                {
                    "memory_id": "m1",
                    "outcome": "helped",
                    "confidence": 0.85,
                    "memory_quote": "Config lives at config/settings.py",
                    "tool_event_ids": [trace[0]["event_id"]],
                    "reasoning": "cited",
                },
                {
                    "memory_id": "m2",
                    "outcome": "helped",
                    "confidence": 0.99,
                    "memory_quote": "Prefer integration tests over mocks",
                    "tool_event_ids": [],
                    "reasoning": "task succeeded so every memory helped",
                },
            ]
        }
    )
    review = _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=trace,
        operation_id="op-1",
    )
    by_id = {verdict.memory_id: verdict for verdict in review.verdicts}
    assert by_id["m1"].outcome == "helped"
    assert by_id["m2"].outcome == "undetermined"


def test_llm_invalidated_requires_failing_event() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    trace = _success_trace()
    client = _FakeClient(
        {
            "verdicts": [
                {
                    "memory_id": "m1",
                    "outcome": "invalidated",
                    "confidence": 0.9,
                    "memory_quote": "Config lives at config/settings.py",
                    "tool_event_ids": [trace[0]["event_id"]],
                    "reasoning": "no failing event cited",
                }
            ]
        }
    )
    review = _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=trace,
        operation_id="op-1",
    )
    assert review.verdicts[0].outcome == "undetermined"


def test_llm_low_confidence_is_downgraded() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    trace = _success_trace()
    client = _FakeClient(
        {
            "verdicts": [
                {
                    "memory_id": "m1",
                    "outcome": "helped",
                    "confidence": 0.2,
                    "memory_quote": "Config lives at config/settings.py",
                    "tool_event_ids": [trace[0]["event_id"]],
                    "reasoning": "weak inference",
                }
            ]
        }
    )
    review = _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=trace,
        operation_id="op-1",
    )
    assert review.verdicts[0].outcome == "undetermined"


def test_llm_invalidated_with_failing_event_is_kept() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    trace = _failure_trace()
    client = _FakeClient(
        {
            "verdicts": [
                {
                    "memory_id": "m1",
                    "outcome": "invalidated",
                    "confidence": 0.9,
                    "memory_quote": "Config lives at config/settings.py",
                    "tool_event_ids": [trace[0]["event_id"]],
                    "reasoning": "path claim failed in this environment",
                }
            ]
        }
    )
    review = _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=trace,
        operation_id="op-1",
    )
    assert review.verdicts[0].outcome == "invalidated"


def test_llm_failure_falls_back_to_rule_verdicts() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    client = _FakeClient(error=TimeoutError("provider down"))
    review = _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=_success_trace(),
        operation_id="op-1",
    )
    assert review.review_source == "rule_fallback"
    assert review.failure_code == "provider_timeout"
    assert review.verdicts[0].outcome == "helped"
    assert review.verdicts[0].basis == "rule"


def test_llm_invalid_json_falls_back_to_rule_verdicts() -> None:
    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    client = _FakeClient(text="not json at all {{{")
    review = _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=_success_trace(),
        operation_id="op-1",
    )
    assert review.review_source == "rule_fallback"
    assert review.verdicts[0].outcome == "helped"


def test_review_without_rendered_memories_reports_no_evidence() -> None:
    receipt = _receipt([])
    review = _rule_reviewer().review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=_success_trace(),
        operation_id="op-1",
    )
    assert review.review_source == "no_evidence"
    assert review.verdicts == ()


def test_review_off_mode_is_noop() -> None:
    reviewer = MemoryContributionReviewer(ContributionReviewConfig(mode="off"))
    review = reviewer.review(
        task_description="t",
        receipt=_receipt([("m1", "v1", "Config lives at config/settings.py")]),
        execution_trace=_success_trace(),
        operation_id="op-1",
    )
    assert review.review_source == "off"
    assert review.verdicts == ()


# ── configuration ──────────────────────────────────────────────────


def test_config_from_runtime_reads_env_and_clamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINI_CODE_MEMORY_CONTRIBUTION_REVIEW", "llm")
    monkeypatch.setenv(
        "MINI_CODE_MEMORY_CONTRIBUTION_REVIEW_MIN_CONFIDENCE", "0.75"
    )
    config = ContributionReviewConfig.from_runtime()
    assert config.mode == "llm"
    assert config.min_confidence == 0.75


def test_config_from_runtime_rejects_unknown_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINI_CODE_MEMORY_CONTRIBUTION_REVIEW", "nonsense")
    config = ContributionReviewConfig.from_runtime()
    assert config.mode == "rule"


# ── budget guard ───────────────────────────────────────────────────


def test_llm_review_respects_exhausted_budget() -> None:
    from minicode.agent_budget import AgentBudgetExceeded, AgentBudgetSnapshot

    class _ExhaustedBudget:
        def reserve_model_call(self, *args, **kwargs):
            raise AgentBudgetExceeded(
                "budget exhausted",
                AgentBudgetSnapshot(
                    limit_total_tokens=1,
                    limit_model_calls=0,
                    limit_cost_usd=0.0,
                    used_total_tokens=1,
                    reserved_total_tokens=0,
                    used_model_calls=0,
                    used_cost_usd=0.0,
                    used_input_tokens=1,
                    used_output_tokens=0,
                ),
            )

    receipt = _receipt([("m1", "v1", "Config lives at config/settings.py")])
    client = _FakeClient({"verdicts": []})
    review = _llm_reviewer(client).review(
        task_description="read the config",
        receipt=receipt,
        execution_trace=_success_trace(),
        operation_id="op-1",
        agent_budget=_ExhaustedBudget(),
    )
    assert review.review_source == "rule_fallback"
    assert review.failure_code == "agent_budget_exhausted"
    # No paid call happens once the shared budget is exhausted.
    assert client.calls == []


# ── feedback application ───────────────────────────────────────────


def _entry_of(manager: MemoryManager, entry_id: str):
    for scope_file in manager.memories.values():
        for entry in scope_file.entries:
            if entry.id == entry_id:
                return entry
    raise AssertionError(f"entry {entry_id} not found")


def test_apply_feedback_helped_records_small_positive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, digest = _add_entry(manager, "Config lives at config/settings.py")
    receipt = _receipt([(entry_id, digest, "Config lives at config/settings.py")])
    review = MemoryContributionReview(
        receipt_id=receipt.receipt_id,
        operation_id="op-1",
        task_summary="read the config",
        verdicts=(_verdict(entry_id, "helped"),),
        review_source="llm",
    )
    applied = apply_contribution_feedback(manager, review, receipt)
    assert applied.helped == 1
    entry = _entry_of(manager, entry_id)
    assert entry.success_count == 1
    # Replaying the same review is idempotent through observation receipts.
    apply_contribution_feedback(manager, review, receipt)
    assert entry.success_count == 1


def test_apply_feedback_invalidated_corroborates_and_quarantines_after_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, digest = _add_entry(manager, "Config lives at config/settings.py")
    receipt = _receipt([(entry_id, digest, "Config lives at config/settings.py")])
    first = MemoryContributionReview(
        receipt_id=receipt.receipt_id,
        operation_id="op-1",
        task_summary="read the config",
        verdicts=(_verdict(entry_id, "invalidated"),),
        review_source="llm",
    )
    apply_contribution_feedback(manager, first, receipt)
    entry = _entry_of(manager, entry_id)
    assert entry.corroborated_failure_count == 1
    assert entry.lifecycle_status != "rejected"

    second = MemoryContributionReview(
        receipt_id="receipt-2",
        operation_id="op-2",
        task_summary="read the config again",
        verdicts=(_verdict(entry_id, "invalidated"),),
        review_source="llm",
    )
    apply_contribution_feedback(manager, second, receipt)
    entry = _entry_of(manager, entry_id)
    assert entry.corroborated_failure_count == 2
    assert entry.lifecycle_status == "rejected"


def test_apply_feedback_undetermined_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, digest = _add_entry(manager, "Prefer integration tests over mocks")
    receipt = _receipt([(entry_id, digest, "Prefer integration tests over mocks")])
    review = MemoryContributionReview(
        receipt_id=receipt.receipt_id,
        operation_id="op-1",
        task_summary="t",
        verdicts=(_verdict(entry_id, "undetermined"),),
        review_source="rule",
    )
    applied = apply_contribution_feedback(manager, review, receipt)
    assert applied.helped == 0
    assert applied.invalidated == 0
    entry = _entry_of(manager, entry_id)
    assert entry.success_count == 0
    assert entry.failure_count == 0
    assert entry.corroborated_failure_count == 0


def test_apply_feedback_version_mismatch_skips_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, _digest = _add_entry(manager, "Config lives at config/settings.py")
    stale_receipt = _receipt([(entry_id, "0" * 64, "old text")])
    review = MemoryContributionReview(
        receipt_id=stale_receipt.receipt_id,
        operation_id="op-1",
        task_summary="t",
        verdicts=(_verdict(entry_id, "helped"),),
        review_source="llm",
    )
    applied = apply_contribution_feedback(manager, review, stale_receipt)
    assert applied.helped == 0
    entry = _entry_of(manager, entry_id)
    assert entry.success_count == 0


# ── pipeline integration ───────────────────────────────────────────


def test_pipeline_review_uses_last_committed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, digest = _add_entry(manager, "Config lives at config/settings.py")
    pipeline = MemoryPipeline(
        manager,
        contribution_reviewer=MemoryContributionReviewer(
            ContributionReviewConfig(mode="rule")
        ),
    )
    pipeline.initialize()
    pipeline._last_committed_receipt = _receipt(
        [(entry_id, digest, "Config lives at config/settings.py")]
    )
    trace = _trace(
        [
            _call(1, "c1", "read", {"path": "config/settings.py"}),
            _result(1, "c1", "read", True, "loaded 30 lines"),
        ]
    )
    review = pipeline.review_injected_memories(
        "read the config",
        trace,
        operation_id="op-pipe",
    )
    assert review is not None
    assert review.verdicts[0].outcome == "helped"
    assert _entry_of(manager, entry_id).success_count == 1
    # Same operation replayed must not double-count.
    pipeline.review_injected_memories(
        "read the config",
        trace,
        operation_id="op-pipe",
    )
    assert _entry_of(manager, entry_id).success_count == 1


def test_pipeline_review_without_receipt_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    pipeline = MemoryPipeline(
        manager,
        contribution_reviewer=MemoryContributionReviewer(
            ContributionReviewConfig(mode="rule")
        ),
    )
    pipeline.initialize()
    assert pipeline.review_injected_memories("t", _success_trace()) is None


def test_agent_loop_delivers_review_before_whole_turn_feedback() -> None:
    from minicode.agent_loop import _deliver_memory_quality_feedback

    calls: list[tuple] = []

    class _Pipeline:
        def review_injected_memories(
            self, task_description, execution_trace=None, **kwargs
        ):
            calls.append(("review", task_description, execution_trace, kwargs))

        def feedback(self, outcome, **kwargs):
            calls.append(("feedback", outcome))

    orch = SimpleNamespace(memory_pipeline=_Pipeline())
    outcome = SimpleNamespace(learning_success=True)
    trace = _success_trace()
    event_sink = SimpleNamespace(feedback_observation_id="obs-1")
    _deliver_memory_quality_feedback(
        orch=orch,
        canonical_outcome=outcome,
        tool_abandoned=False,
        verification_passed=0,
        verification_failed=0,
        event_sink=event_sink,
        task_description="read the config",
        execution_trace=trace,
        agent_budget="budget-token",
    )
    assert [name for name, *_ in calls] == ["review", "feedback"]
    assert calls[0][1] == "read the config"
    assert calls[0][2] is trace
    assert calls[0][3].get("observation_id") == "obs-1"
    # Observability: the review must reach the run journal and the shared
    # budget, exactly like every other end-of-turn model consumer.
    assert calls[0][3].get("event_sink") is event_sink
    assert calls[0][3].get("agent_budget") == "budget-token"


def test_agent_loop_review_failure_does_not_block_feedback() -> None:
    from minicode.agent_loop import _deliver_memory_quality_feedback

    delivered: list[str] = []

    class _Pipeline:
        def review_injected_memories(self, *args, **kwargs):
            raise RuntimeError("review exploded")

        def feedback(self, outcome, **kwargs):
            delivered.append(str(outcome))

    orch = SimpleNamespace(memory_pipeline=_Pipeline())
    _deliver_memory_quality_feedback(
        orch=orch,
        canonical_outcome=SimpleNamespace(learning_success=True),
        tool_abandoned=False,
        verification_passed=0,
        verification_failed=0,
        event_sink=None,
        task_description="t",
        execution_trace=_success_trace(),
    )
    assert delivered == ["True"]


def test_agent_loop_without_trace_skips_review() -> None:
    from minicode.agent_loop import _deliver_memory_quality_feedback

    calls: list[str] = []

    class _Pipeline:
        def review_injected_memories(self, *args, **kwargs):
            calls.append("review")

        def feedback(self, outcome, **kwargs):
            calls.append("feedback")

    orch = SimpleNamespace(memory_pipeline=_Pipeline())
    _deliver_memory_quality_feedback(
        orch=orch,
        canonical_outcome=SimpleNamespace(learning_success=True),
        tool_abandoned=False,
        verification_passed=0,
        verification_failed=0,
        event_sink=None,
        task_description="t",
        execution_trace=None,
    )
    assert calls == ["feedback"]
