from __future__ import annotations

import inspect
import os
import random
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from minicode.memory_candidate_consolidation import CandidateConsolidator
from minicode.memory_retrieval import (
    CanonicalMemoryRetriever,
    MemoryRetrievalRequest,
    RetrievedMemory,
    RetrievalScore,
)
from scripts.memory_retrieval_phase2b_evaluator import (
    REFERENCE_TIME,
    _evaluate_holdout_case,
    _isolated_pipeline,
    load_holdout,
)


ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = ROOT / "tests" / "fixtures" / "memory_retrieval_phase2b_holdout.json"


def _cases() -> list[dict]:
    return load_holdout(HOLDOUT)


def _case(case_id: str) -> dict:
    return next(case for case in _cases() if case["case_id"] == case_id)


def _candidate(index: int, *, rank: int | None = None) -> RetrievedMemory:
    return RetrievedMemory(
        entry_id=f"state-{index:04d}",
        scope=("project", "local", "user")[index % 3],
        category=("architecture", "testing", "decision")[index % 3],
        content=f"Object object{index // 4} operation value{index} is deterministic.",
        score=RetrievalScore(
            lexical_score=0.8,
            final_score=0.8 - index / 10000,
            matched_terms=(f"object-{index // 4}", "operation"),
        ),
        rank=rank if rank is not None else index + 1,
        token_count=12,
        truncated=False,
        source="synthetic",
        tags=(f"object-{index // 4}",),
        domains=("backend",),
    )


def test_consolidator_has_one_post_gate_pre_controller_integration_point() -> None:
    # ``retrieve`` owns the revision-fenced snapshot; the actual pipeline is
    # intentionally isolated in this helper so the lock covers every stage.
    source = inspect.getsource(CanonicalMemoryRetriever._retrieve_snapshot)

    assert source.count("self._consolidator.consolidate(") == 1
    assert source.index("selected_after_gate") < source.index("self._consolidator.consolidate(")
    assert source.index("self._consolidator.consolidate(") < source.index("self._decision(")
    assert source.index("self._decision(") < source.index("self._render(")


def test_suppressed_ids_never_reach_prompt_counters_or_feedback() -> None:
    case = _case("p2b-conflict-01")

    result = _evaluate_holdout_case(case)

    suppressed = {item["entry_id"] for item in result["suppressed"]}
    assert suppressed == set(case["must_exclude_ids"])
    assert suppressed.isdisjoint(result["rendered_ids"])
    assert suppressed.isdisjoint(result["recorded_ids"])
    assert suppressed.isdisjoint(result["feedback_ids"])
    assert result["rendered_ids"] == result["recorded_ids"] == result["feedback_ids"]


def test_unresolved_conflict_fails_closed_before_counters() -> None:
    case = _case("p2b-ambiguous-01")

    result = _evaluate_holdout_case(case)

    assert result["post_consolidation_ids"] == []
    assert result["rendered_ids"] == result["recorded_ids"] == result["feedback_ids"] == []
    assert result["no_match"] is True
    assert result["no_match_reason"] == "unresolved_conflict_fail_closed"
    assert {item["reason"] for item in result["suppressed"]} == {"unresolved_conflict"}


def test_queryless_controller_disabled_and_high_pressure_remain_fail_closed() -> None:
    case = _case("p2b-complement-01")
    with _isolated_pipeline(case) as (manager, _pipeline), patch(
        "minicode.memory.time.time", return_value=REFERENCE_TIME
    ), patch("minicode.memory_retrieval.time.time", return_value=REFERENCE_TIME):
        retriever = CanonicalMemoryRetriever(manager)
        queryless = retriever.retrieve(MemoryRetrievalRequest(query=""))
        disabled = retriever.retrieve(
            MemoryRetrievalRequest(query=case["query"], max_memories=0)
        )
        pressure = retriever.retrieve(
            MemoryRetrievalRequest(query=case["query"], context_usage=1.0)
        )

    assert queryless.no_match_reason == "queryless_production_request"
    assert queryless.rendered_ids == ()
    assert disabled.no_match_reason == "controller_disabled"
    assert disabled.rendered_ids == ()
    assert pressure.no_match_reason == "controller_disabled"
    assert pressure.rendered_ids == ()


def test_hard_count_budget_runs_after_consolidation() -> None:
    case = _case("p2b-budget-01")

    result = _evaluate_holdout_case(case)

    assert result["post_consolidation_ids"] == [
        "p2b-budget-01-primary",
        "p2b-budget-01-secondary",
    ]
    assert result["rendered_ids"] == ["p2b-budget-01-primary"]
    assert len(result["rendered_ids"]) <= case["max_memories"]
    assert result["prompt_tokens"] <= case["max_tokens"]


def test_fixed_seed_candidate_state_machine_preserves_invariants() -> None:
    randomizer = random.Random(20260715)
    consolidator = CandidateConsolidator(max_candidates=32)
    request = MemoryRetrievalRequest(query="Review object operation settings")
    pool = [_candidate(index) for index in range(80)]

    for _ in range(100):
        randomizer.shuffle(pool)
        count = randomizer.randint(0, len(pool))
        candidates = tuple(pool[:count])
        before = tuple(candidates)
        first = consolidator.consolidate(candidates, request)
        second = consolidator.consolidate(tuple(reversed(candidates)), request)
        input_ids = {candidate.entry_id for candidate in candidates}

        assert candidates == before
        assert first == second
        assert set(first.retained_ids).isdisjoint(first.suppressed_ids)
        assert set(first.retained_ids) | set(first.suppressed_ids) == input_ids
        assert len(first.retained_ids) <= 32
        assert len(first.retained_ids) == len(set(first.retained_ids))


def test_rank_then_entry_id_is_the_stable_final_tie_breaker() -> None:
    request = MemoryRetrievalRequest(query="Review object operation settings")
    tied_score = RetrievalScore(
        lexical_score=0.8,
        final_score=0.8,
        matched_terms=("object", "operation"),
    )
    same_rank = tuple(
        replace(_candidate(index), rank=1, score=tied_score) for index in (3, 1, 2)
    )

    result = CandidateConsolidator().consolidate(same_rank, request)

    assert result.retained_ids == ("state-0001", "state-0002", "state-0003")


def test_different_python_hash_seeds_produce_identical_result() -> None:
    script = """
import json
from minicode.memory_candidate_consolidation import CandidateConsolidator
from minicode.memory_retrieval import MemoryRetrievalRequest, RetrievedMemory, RetrievalScore
items=[]
for index in range(40):
    items.append(RetrievedMemory(
        entry_id=f'item-{39-index:03d}', scope='project', category='decision',
        content=f'Object object{index//4} operation value{index}.',
        score=RetrievalScore(final_score=0.8, matched_terms=(f'object-{index//4}', 'operation')),
        rank=1, token_count=8, truncated=False, source='synthetic',
        tags=(f'object-{index//4}',), domains=('backend',)))
result=CandidateConsolidator(max_candidates=32).consolidate(
    tuple(items), MemoryRetrievalRequest(query='Review object operation settings'))
print(json.dumps({'retained': result.retained_ids, 'suppressed': [x.to_dict() for x in result.suppressed]}, sort_keys=True))
"""
    outputs = []
    for seed in ("1", "7", "123"):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        outputs.append(completed.stdout)

    assert len(set(outputs)) == 1
