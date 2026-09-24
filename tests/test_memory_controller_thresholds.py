from __future__ import annotations

from pathlib import Path

import pytest

from minicode.memory import MemoryEntry, MemoryManager, MemoryScope, MemoryTier
from minicode.memory_hybrid import HybridAdjudication, HybridCandidateSignal
from minicode.memory_injector import (
    MemoryInjectionController,
    MemoryInjectionSignal,
    MemoryInjector,
)
from minicode.memory_retrieval import (
    CanonicalMemoryRetriever,
    MemoryRetrievalRequest,
    RetrievalSource,
)


def _manager(tmp_path: Path) -> MemoryManager:
    manager = MemoryManager(
        project_root=tmp_path / "workspace",
        data_root=tmp_path / "memory-home",
    )
    entry = MemoryEntry(
        id="checkout-guidance",
        scope=MemoryScope.PROJECT,
        category="pattern",
        content="checkout guidance",
        tags=["checkout"],
        tier=MemoryTier.SHORT_TERM,
        lifecycle_status="active",
        approval_status="approved",
        safety_status="safe",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    for memory_file in manager.memories.values():
        memory_file._rebuild_indices()
    return manager


def test_context_pressure_threshold_controls_canonical_admission(
    tmp_path: Path,
) -> None:
    retriever = CanonicalMemoryRetriever(_manager(tmp_path))
    request_values = {
        "query": "checkout zebra",
        "max_memories": 5,
        "max_total_tokens": 800,
        "max_tokens_per_memory": 160,
        "min_relevance": 0.3,
    }

    normal = retriever.retrieve(
        MemoryRetrievalRequest(context_usage=0.4, **request_values)
    )
    pressured = retriever.retrieve(
        MemoryRetrievalRequest(context_usage=0.8, **request_values)
    )

    assert normal.rendered_ids == ("checkout-guidance",)
    assert normal.candidates[0].score.final_score < 0.55
    assert pressured.controller_decision["min_relevance"] == 0.55
    assert pressured.rendered_ids == ()
    assert pressured.no_match_reason == "relevance_gate_rejected_all"


def test_injector_forwards_correction_risk_to_canonical_admission(
    tmp_path: Path,
) -> None:
    injector = MemoryInjector(
        _manager(tmp_path),
        min_relevance=0.3,
        injection_cooldown=0.0,
    )

    baseline = injector.inject_for_task("checkout zebra")
    corrected = injector.inject_for_task(
        "checkout zebra",
        signal=MemoryInjectionSignal(user_correction_count=3),
    )

    assert [memory.entry_id for memory in baseline] == ["checkout-guidance"]
    assert injector.last_decision is not None
    assert injector.last_decision.min_relevance == pytest.approx(0.6)
    assert corrected == []


def test_context_pressure_threshold_also_controls_hybrid_admission(
    tmp_path: Path,
) -> None:
    class AcceptedBelowThreshold:
        def __init__(self) -> None:
            self.lexical_accepted_ids: tuple[str, ...] = ()

        def adjudicate(self, **kwargs: object) -> HybridAdjudication:
            entries = kwargs["entries"]
            assert isinstance(entries, tuple)
            lexical_accepted_ids = kwargs["lexical_accepted_ids"]
            assert isinstance(lexical_accepted_ids, tuple)
            self.lexical_accepted_ids = lexical_accepted_ids
            return HybridAdjudication(
                signals=tuple(
                    HybridCandidateSignal(
                        entry_id=entry.id,
                        dense_score=0.4,
                        relevance_score=0.4,
                        accepted=True,
                        reason_codes=("semantic_match",),
                    )
                    for entry in entries
                ),
                diagnostics={"provider": "controlled-test"},
            )

    provider = AcceptedBelowThreshold()
    retriever = CanonicalMemoryRetriever(
        _manager(tmp_path),
        hybrid_provider=provider,
    )

    result = retriever.retrieve(
        MemoryRetrievalRequest(
            query="checkout zebra",
            context_usage=0.8,
            min_relevance=0.3,
            max_total_tokens=800,
            max_tokens_per_memory=160,
        )
    )

    assert result.controller_decision["min_relevance"] == 0.55
    assert provider.lexical_accepted_ids == ("checkout-guidance",)
    assert result.candidates[0].score.final_score < 0.55
    assert result.rendered_ids == ()
    assert result.no_match_reason == "relevance_gate_rejected_all"


def test_new_control_fields_do_not_shift_legacy_positional_arguments() -> None:
    request = MemoryRetrievalRequest(
        "checkout zebra",
        (),
        (),
        0.4,
        5,
        800,
        160,
        0.3,
        RetrievalSource.CANONICAL,
        False,
        "verified outcome",
        True,
    )

    assert request.task_outcome_context == "verified outcome"
    assert request.allow_queryless is True
    assert request.retrieval_quality == 0.5
    assert request.user_correction_count == 0
    assert request.task_repetition is False


@pytest.mark.parametrize(
    "signal",
    (
        MemoryInjectionSignal(retrieval_quality=0.9),
        MemoryInjectionSignal(recent_failure=True),
        MemoryInjectionSignal(task_repetition=True),
        MemoryInjectionSignal(
            retrieval_quality=0.9,
            recent_failure=True,
            task_repetition=True,
        ),
    ),
)
def test_controller_never_lowers_semantic_admission_floor(
    signal: MemoryInjectionSignal,
) -> None:
    decision = MemoryInjectionController().decide(
        signal,
        base_max_memories=5,
        base_min_relevance=0.3,
        base_max_tokens=200,
    )

    assert decision.min_relevance >= 0.3


def test_disabled_controller_skips_hybrid_provider_entirely(tmp_path: Path) -> None:
    class CountingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def adjudicate(self, **kwargs: object) -> HybridAdjudication:
            del kwargs
            self.calls += 1
            return HybridAdjudication(signals=(), diagnostics={})

    provider = CountingProvider()
    retriever = CanonicalMemoryRetriever(
        _manager(tmp_path),
        hybrid_provider=provider,
    )

    result = retriever.retrieve(
        MemoryRetrievalRequest(
            query="checkout zebra",
            context_usage=0.95,
        )
    )

    assert result.no_match_reason == "controller_disabled"
    assert result.rendered_ids == ()
    assert provider.calls == 0
