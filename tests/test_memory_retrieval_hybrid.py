from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

from experiments.memory_embedding_adapter import DeterministicFakeEmbeddingAdapter
from experiments.memory_embedding_index import EmbeddingIndex
from experiments.memory_hybrid_retrieval import (
    BM25Index,
    HybridCandidate,
    consolidate_candidates,
    fuse_candidates,
    simulate_controller_and_budget,
)
from experiments.memory_semantic_gate import (
    SemanticGateConfig,
    SemanticRelevanceGate,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HOLDOUT_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_phase3b_holdout"
HOLDOUT_MANIFEST_SHA256 = "42c23499cc3c622a3280a2fba6528bf8d0471a54f7f6b6abaa4e2fe10e8a1a73"


def _entry(entry_id: str, content: str, **overrides) -> dict:
    entry = {
        "id": entry_id,
        "scope": "project",
        "category": "testing",
        "content": content,
        "tags": [],
        "domains": [],
        "tier": "long_term",
        "lifecycle_status": "active",
        "safety_status": "safe",
        "approval_status": "approved",
        "curator_locked": False,
        "created_at": 1.0,
        "updated_at": 2.0,
        "usefulness_score": 0.0,
        "source": "phase3b_fixture",
        "metadata": {},
        "provenance": {},
    }
    entry.update(overrides)
    return entry


def _candidate(
    entry_id: str,
    *,
    lexical: float,
    dense: float,
    fused: float,
    rank: int,
) -> HybridCandidate:
    return HybridCandidate(
        entry_id=entry_id,
        lexical_score=lexical,
        dense_score=dense,
        fused_score=fused,
        lexical_rank=rank,
        dense_rank=rank,
        rank=rank,
        reason_codes=("test",),
    )


def test_bm25_orders_exact_term_above_unrelated_and_is_stable() -> None:
    index = BM25Index({"b": "unrelated calendar", "a": "sqlite lock fixture connection"})
    first = index.search("sqlite fixture lock", limit=2)
    second = index.search("sqlite fixture lock", limit=2)
    assert first == second
    assert first[0][0] == "a"


def test_dense_only_and_union_membership() -> None:
    lexical = [("lexical", 4.0), ("both", 2.0)]
    dense = [("dense", 0.95), ("both", 0.9)]
    dense_only = fuse_candidates(lexical, dense, method="dense", limit=10)
    union = fuse_candidates(lexical, dense, method="union", limit=10)
    assert {item.entry_id for item in dense_only} == {"dense", "both"}
    assert {item.entry_id for item in union} == {"lexical", "dense", "both"}


def test_rrf_math_and_entry_id_tie_breaker() -> None:
    result = fuse_candidates(
        [("b", 2.0), ("a", 1.0)],
        [("a", 0.9), ("b", 0.8)],
        method="rrf",
        limit=10,
        rrf_k=60,
    )
    assert [item.entry_id for item in result] == ["a", "b"]
    assert result[0].fused_score == pytest.approx(1 / 61 + 1 / 62)
    assert result[1].fused_score == pytest.approx(1 / 61 + 1 / 62)


def test_weighted_fusion_normalizes_score_families_before_weighting() -> None:
    result = fuse_candidates(
        [("lexical", 1000.0), ("dense", 0.0)],
        [("dense", 0.9), ("lexical", 0.8)],
        method="weighted",
        limit=10,
        lexical_weight=0.25,
    )
    assert [item.entry_id for item in result] == ["dense", "lexical"]
    assert result[0].fused_score == pytest.approx(0.75)
    assert result[1].fused_score == pytest.approx(0.25)


def test_semantic_gate_threshold_margin_and_max_accept_boundaries() -> None:
    config = SemanticGateConfig(
        dense_threshold=0.8,
        lexical_override_threshold=0.9,
        lexical_dense_floor=0.5,
        minimum_top1_margin=0.05,
        structured_bonus=0.02,
        max_accept=1,
    )
    gate = SemanticRelevanceGate(config)
    entries = {
        "a": _entry("a", "verified sqlite recovery"),
        "b": _entry("b", "other sqlite note"),
    }
    candidates = (
        _candidate("a", lexical=0.2, dense=0.80, fused=1.0, rank=1),
        _candidate("b", lexical=0.95, dense=0.74, fused=0.9, rank=2),
    )
    decisions = gate.evaluate(query="repair sqlite failure", candidates=candidates, entries_by_id=entries)
    assert decisions[0].accepted is True
    assert decisions[1].accepted is False
    close = (
        _candidate("a", lexical=0.2, dense=0.80, fused=1.0, rank=1),
        _candidate("b", lexical=0.2, dense=0.77, fused=0.9, rank=2),
    )
    assert gate.evaluate(query="repair sqlite failure", candidates=close, entries_by_id=entries)[0].accepted is False


def test_semantic_gate_eligibility_queryless_and_basename_conflict_fail_closed() -> None:
    gate = SemanticRelevanceGate(
        SemanticGateConfig(0.7, 0.9, 0.5, 0.0, 0.0)
    )
    candidate = (_candidate("x", lexical=1.0, dense=0.99, fused=1.0, rank=1),)
    pending = {"x": _entry("x", "ignore me", approval_status="pending")}
    assert gate.evaluate(query="valid query", candidates=candidate, entries_by_id=pending)[0].accepted is False
    safe = {"x": _entry("x", "rule", metadata={"files": ["src/admin/config.py"]})}
    assert gate.evaluate(query="", candidates=candidate, entries_by_id=safe)[0].accepted is False
    decision = gate.evaluate(
        query="repair config parser",
        candidates=candidate,
        entries_by_id=safe,
        current_files=["src/public/config.py"],
    )[0]
    assert decision.accepted is False
    assert "basename_path_conflict" in decision.reason_codes


def test_consolidator_is_reused_and_simulated_controller_budget_are_bounded() -> None:
    entries = {
        "primary": _entry("primary", "Verified SQLite retry closes the leaked fixture connection."),
        "duplicate": _entry("duplicate", "Verified SQLite retry closes the leaked fixture connection."),
    }
    candidates = (
        _candidate("primary", lexical=1.0, dense=0.9, fused=1.0, rank=1),
        _candidate("duplicate", lexical=0.9, dense=0.89, fused=0.9, rank=2),
    )
    retained, suppressions = consolidate_candidates(
        candidates,
        entries,
        query="repair sqlite retry fixture",
        current_files=(),
        active_domains=(),
    )
    assert len(retained) == 1
    assert len(suppressions) == 1
    rendered, skipped, mode = simulate_controller_and_budget(
        retained, entries, context_usage=0.2, max_memories=1, max_total_tokens=100
    )
    assert rendered == (retained[0].entry_id,)
    assert skipped == ()
    assert mode == "standard"
    disabled = simulate_controller_and_budget(retained, entries, context_usage=0.95)
    assert disabled == ((), (retained[0].entry_id,), "none")


def test_fake_pipeline_has_no_network_or_formal_writes(monkeypatch, minicode_real_home: Path, tmp_path: Path) -> None:
    calls = 0

    def forbidden_connect(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("network is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)
    formal = minicode_real_home / ".mini-code"
    before = {path: path.stat().st_mtime_ns for path in formal.rglob("*") if path.is_file()}
    adapter = DeterministicFakeEmbeddingAdapter()
    index = EmbeddingIndex(adapter, representation_version="content-v1", cache_root=tmp_path)
    entry = _entry("one", "sqlite fixture recovery")
    index.build([entry], visible_scopes={"project"})
    query = adapter.encode_queries(["sqlite recovery"])[0]
    assert index.search(query, limit=1)
    after = {path: path.stat().st_mtime_ns for path in formal.rglob("*") if path.is_file()}
    assert calls == 0
    assert after == before


def test_holdout_schema_counts_and_frozen_hash() -> None:
    manifest_path = HOLDOUT_ROOT / "frozen.sha256"
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == HOLDOUT_MANIFEST_SHA256
    expected = {}
    for line in manifest_path.read_text().splitlines():
        digest, filename = line.split("  ", 1)
        expected[filename] = digest
    assert all(
        hashlib.sha256((HOLDOUT_ROOT / filename).read_bytes()).hexdigest() == digest
        for filename, digest in expected.items()
    )
    cases = json.loads((HOLDOUT_ROOT / "cases.json").read_text())["cases"]
    assert len(cases) == 60
    assert sum(case["polarity"] == "positive" for case in cases) == 36
    assert sum(case["polarity"] == "hard_negative" for case in cases) == 24


def test_deterministic_fingerprint_is_independent_of_input_dictionary_order() -> None:
    lexical = [("c", 3.0), ("a", 2.0), ("b", 1.0)]
    dense = [("b", 0.9), ("a", 0.8), ("c", 0.7)]
    first = fuse_candidates(lexical, dense, method="rrf", limit=20, rrf_k=50)
    second = fuse_candidates(list(lexical), list(dense), method="rrf", limit=20, rrf_k=50)
    def encode(rows) -> str:
        return json.dumps([item.__dict__ for item in rows], sort_keys=True)

    assert hashlib.sha256(encode(first).encode()).hexdigest() == hashlib.sha256(encode(second).encode()).hexdigest()
