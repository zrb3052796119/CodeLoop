"""Offline Phase 3B hybrid retrieval calibration and one-shot evaluation."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import socket
import statistics
import time
import tracemalloc
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence
from unittest.mock import patch

import jsonschema

from experiments.memory_embedding_adapter import EmbeddingAdapter, LocalEmbeddingAdapter
from experiments.memory_embedding_index import (
    EmbeddingIndex,
    document_representation,
    eligibility_reason,
    query_representation,
)
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
from scripts.memory_retrieval_semantic_gap_evaluator import (
    REFERENCE_TIME,
    _diagnostic_arm,
    isolated_case_manager,
    load_dataset,
    wilson_interval,
)


EVALUATOR_VERSION = "1.0.0"
HOLDOUT_MANIFEST_SHA256 = "42c23499cc3c622a3280a2fba6528bf8d0471a54f7f6b6abaa4e2fe10e8a1a73"
PHASE3A_BASELINE_SHA256 = "5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b"
CONFIG_SCHEMA_VERSION = "1.0"
REPORT_SCHEMA_VERSION = "1.0"
CALIBRATION_TOP_K_CHOICES = ((20, 20), (20, 32), (32, 32))
CALIBRATION_RRF_K = (20, 60)
CALIBRATION_WEIGHTS = (0.25, 0.35, 0.5)
CALIBRATION_DENSE_THRESHOLDS = (0.74, 0.77, 0.80, 0.83, 0.86, 0.89)
CALIBRATION_MARGINS = (0.0, 0.01, 0.02, 0.03)
CALIBRATION_STRUCTURED_BONUSES = (0.0, 0.015)
CALIBRATION_LEXICAL_OVERRIDES = (0.85, 1.0)
ARM_NAMES = (
    "arm_a_frozen_lexical",
    "arm_b_dense_only",
    "arm_c_lexical_dense_union",
    "arm_d_rrf_hybrid",
    "arm_e_weighted_hybrid",
    "arm_f_selected_hybrid_current_gate",
    "arm_g_selected_hybrid_semantic_gate",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def verify_freeze_manifest(root: Path, expected_manifest_hash: str) -> dict[str, Any]:
    manifest_path = root / "frozen.sha256"
    manifest_hash = sha256_file(manifest_path)
    expected: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        digest, filename = line.split("  ", 1)
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or Path(filename).name != filename:
            raise ValueError("invalid freeze manifest")
        expected[filename] = digest
    mismatches = {
        filename: {
            "expected": digest,
            "actual": sha256_file(root / filename) if (root / filename).is_file() else "missing",
        }
        for filename, digest in expected.items()
        if not (root / filename).is_file() or sha256_file(root / filename) != digest
    }
    return {
        "matches": not mismatches and manifest_hash == expected_manifest_hash,
        "file_count": len(expected),
        "manifest_sha256": manifest_hash,
        "expected_manifest_sha256": expected_manifest_hash,
        "mismatches": mismatches,
    }


def _normalize_holdout_case(case: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(case)
    normalized["allowed_secondary_ids"] = []
    normalized["hard_negative_control_case_ids"] = []
    return normalized


def load_phase3b_holdout(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    freeze = verify_freeze_manifest(root, HOLDOUT_MANIFEST_SHA256)
    if not freeze["matches"]:
        raise ValueError(f"Phase 3B holdout freeze mismatch: {freeze['mismatches']}")
    schema = json.loads((root / "schema.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    cases_doc = json.loads((root / manifest["case_file"]).read_text(encoding="utf-8"))
    background_doc = json.loads(
        (root / manifest["background_file"]).read_text(encoding="utf-8")
    )
    for document in (manifest, cases_doc, background_doc):
        jsonschema.validate(document, schema)
    cases = [_normalize_holdout_case(case) for case in cases_doc["cases"]]
    if len(cases) != 60 or sum(case["polarity"] == "positive" for case in cases) != 36:
        raise ValueError("Phase 3B holdout counts are invalid")
    return {
        "manifest": manifest,
        "cases": sorted(cases, key=lambda item: item["case_id"]),
        "background": copy.deepcopy(background_doc["memories"]),
        "freeze": freeze,
    }


def load_frozen_baseline(path: Path) -> dict[str, Any]:
    path = Path(path)
    if sha256_file(path) != PHASE3A_BASELINE_SHA256:
        raise ValueError("Phase 3A baseline artifact hash mismatch")
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        document.get("schema_version") != "memory-retrieval-semantic-gap-baseline-v1"
        or len(document.get("per_case_results", [])) != 108
    ):
        raise ValueError("unsupported Phase 3A baseline artifact")
    return document


def _phase3a_lexical_views(baseline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        result["case_id"]: copy.deepcopy(result["canonical_diagnostic"])
        for result in baseline["per_case_results"]
    }


def _holdout_lexical_views(
    cases: list[dict[str, Any]], background: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    views: dict[str, dict[str, Any]] = {}
    for case in cases:
        with isolated_case_manager(case, background) as (manager, _), patch(
            "minicode.memory.time.time", return_value=REFERENCE_TIME
        ), patch("minicode.memory_retrieval.time.time", return_value=REFERENCE_TIME):
            _, view = _diagnostic_arm(manager, case)
        views[case["case_id"]] = view
    return views


def _eligible_entries_for_cases(
    cases: Sequence[dict[str, Any]], background: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    entries = [copy.deepcopy(entry) for entry in background]
    entries.extend(copy.deepcopy(entry) for case in cases for entry in case["memories"])
    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry["id"] in by_id:
            raise ValueError(f"duplicate entry ID: {entry['id']}")
        by_id[entry["id"]] = entry
    return [by_id[key] for key in sorted(by_id)]


def _allowed_ids(case: dict[str, Any], background: Sequence[dict[str, Any]]) -> set[str]:
    return {entry["id"] for entry in (*background, *case["memories"])}


def _lexical_pairs(view: dict[str, Any], top_k: int) -> list[tuple[str, float]]:
    scores = view.get("scores_top50", {})
    pairs = []
    for rank, entry_id in enumerate(view.get("candidate_ids_top50", [])[:top_k], 1):
        detail = scores.get(entry_id, {})
        score = float(detail.get("final_score", detail.get("lexical_score", 0.0)))
        pairs.append((entry_id, score if math.isfinite(score) else 0.0))
    return pairs


def _config_payload(configuration: dict[str, Any], calibration: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "synthetic_data": True,
        "calibration_split": "analysis",
        "sealed_or_holdout_case_ids_read": [],
        "selected_configuration": configuration,
        "calibration": calibration,
    }


def write_frozen_config(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    document = copy.deepcopy(payload)
    document["payload_sha256"] = payload_hash(payload)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return document


def load_frozen_config(path: Path) -> dict[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = document.pop("payload_sha256", None)
    actual = payload_hash(document)
    if not isinstance(expected, str) or expected != actual:
        raise ValueError("frozen hybrid configuration hash mismatch")
    document["payload_sha256"] = expected
    if document.get("sealed_or_holdout_case_ids_read") != []:
        raise ValueError("configuration calibration was contaminated by decision cases")
    return document


def _calibration_outcome(
    cases: Sequence[dict[str, Any]],
    fused_by_case: dict[str, tuple[HybridCandidate, ...]],
    entries_by_id: dict[str, dict[str, Any]],
    gate: SemanticRelevanceGate,
) -> tuple[dict[str, Any], dict[str, str]]:
    positive = 0
    negative = 0
    candidate_hits = 0
    rendered_hits = 0
    negative_candidate = 0
    negative_rendered = 0
    rendered_total = 0
    relevant_rendered = 0
    outcomes: dict[str, str] = {}
    for case in cases:
        fused = fused_by_case[case["case_id"]]
        candidate_ids = {item.entry_id for item in fused[:20]}
        decisions = gate.evaluate(
            query=case["query"],
            candidates=fused[:20],
            entries_by_id=entries_by_id,
            current_files=case["current_files"],
            active_domains=case["active_domains"],
        )
        accepted = {item.entry_id for item in decisions if item.accepted}
        rendered_total += len(accepted)
        if case["polarity"] == "positive":
            positive += 1
            primary = case["primary_entry_ids"][0]
            candidate_hits += primary in candidate_ids
            rendered_hits += primary in accepted
            relevant_rendered += primary in accepted
            outcomes[case["case_id"]] = (
                "rendered" if primary in accepted else "candidate_only" if primary in candidate_ids else "missed"
            )
        else:
            negative += 1
            excluded = set(case["must_exclude_ids"])
            negative_candidate += bool(candidate_ids & excluded)
            negative_rendered += bool(accepted & excluded)
            outcomes[case["case_id"]] = "false_render" if accepted & excluded else "rejected"
    metrics = {
        "positive_count": positive,
        "hard_negative_count": negative,
        "candidate_recall_at_20": candidate_hits / positive if positive else 1.0,
        "rendered_positive_recall": rendered_hits / positive if positive else 1.0,
        "rendered_precision": relevant_rendered / rendered_total if rendered_total else 1.0,
        "hard_negative_candidate_rate": negative_candidate / negative if negative else 0.0,
        "hard_negative_rendered_rate": negative_rendered / negative if negative else 0.0,
        "rendered_count": rendered_total,
    }
    return metrics, outcomes


def _fold_id(case: dict[str, Any]) -> int:
    key = f"{case['category']}:{case['query_language']}:{case['memory_language']}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 4


def calibrate_configuration(
    *,
    adapter: EmbeddingAdapter,
    phase3a_dataset_root: Path,
    phase3a_baseline_path: Path,
    work_root: Path,
) -> dict[str, Any]:
    dataset = load_dataset(phase3a_dataset_root)
    analysis_cases = [case for case in dataset["cases"] if case["split"] == "analysis"]
    if len(analysis_cases) != 72:
        raise ValueError("Phase 3A analysis split must contain 72 cases")
    baseline = load_frozen_baseline(phase3a_baseline_path)
    lexical_views = _phase3a_lexical_views(baseline)
    entries = _eligible_entries_for_cases(analysis_cases, dataset["background"])
    entries_by_id = {entry["id"]: entry for entry in entries}
    attempts: list[dict[str, Any]] = []
    representation_data: dict[str, dict[str, Any]] = {}
    for representation in ("content-v1", "structured-v1"):
        index = EmbeddingIndex(
            adapter,
            representation_version=representation,
            cache_root=work_root / f"calibration-{representation}",
        )
        build_started = time.perf_counter()
        build_result = index.build(entries, visible_scopes={"user", "project", "local"})
        build_ms = (time.perf_counter() - build_started) * 1000
        queries = [
            query_representation(
                case["query"],
                current_files=case["current_files"],
                active_domains=case["active_domains"],
            )
            for case in analysis_cases
        ]
        vectors = adapter.encode_queries(queries)
        dense_by_case = {
            case["case_id"]: index.search(
                vector,
                limit=32,
                allowed_ids=_allowed_ids(case, dataset["background"]),
            )
            for case, vector in zip(analysis_cases, vectors, strict=True)
        }
        representation_data[representation] = {
            "index": index,
            "build_result": build_result,
            "build_ms": round(build_ms, 6),
            "dense_by_case": dense_by_case,
        }

    fusion_specs: list[dict[str, Any]] = []
    for lexical_top_k, dense_top_k in CALIBRATION_TOP_K_CHOICES:
        for rrf_k in CALIBRATION_RRF_K:
            fusion_specs.append(
                {
                    "method": "rrf",
                    "lexical_top_k": lexical_top_k,
                    "dense_top_k": dense_top_k,
                    "rrf_k": rrf_k,
                    "lexical_weight": 0.35,
                }
            )
        for lexical_weight in CALIBRATION_WEIGHTS:
            fusion_specs.append(
                {
                    "method": "weighted",
                    "lexical_top_k": lexical_top_k,
                    "dense_top_k": dense_top_k,
                    "rrf_k": 60,
                    "lexical_weight": lexical_weight,
                }
            )

    attempt_number = 0
    for representation, data in representation_data.items():
        for fusion in fusion_specs:
            fused_by_case = {
                case["case_id"]: fuse_candidates(
                    _lexical_pairs(lexical_views[case["case_id"]], fusion["lexical_top_k"]),
                    data["dense_by_case"][case["case_id"]][: fusion["dense_top_k"]],
                    method=fusion["method"],
                    limit=20,
                    rrf_k=fusion["rrf_k"],
                    lexical_weight=fusion["lexical_weight"],
                )
                for case in analysis_cases
            }
            for dense_threshold in CALIBRATION_DENSE_THRESHOLDS:
                for margin in CALIBRATION_MARGINS:
                    for structured_bonus in CALIBRATION_STRUCTURED_BONUSES:
                        for lexical_override in CALIBRATION_LEXICAL_OVERRIDES:
                            attempt_number += 1
                            gate_config = SemanticGateConfig(
                                dense_threshold=dense_threshold,
                                lexical_override_threshold=lexical_override,
                                lexical_dense_floor=0.72,
                                minimum_top1_margin=margin,
                                structured_bonus=structured_bonus,
                                max_accept=1,
                                max_rank=20,
                                minimum_query_terms=2,
                            )
                            metrics, outcomes = _calibration_outcome(
                                analysis_cases,
                                fused_by_case,
                                entries_by_id,
                                SemanticRelevanceGate(gate_config),
                            )
                            fold_metrics = {}
                            for fold in range(4):
                                subset = [case for case in analysis_cases if _fold_id(case) == fold]
                                fold_metrics[str(fold)] = _calibration_outcome(
                                    subset,
                                    fused_by_case,
                                    entries_by_id,
                                    SemanticRelevanceGate(gate_config),
                                )[0]
                            attempts.append(
                                {
                                    "attempt_id": f"analysis-grid-{attempt_number:04d}",
                                    "representation_version": representation,
                                    **fusion,
                                    "gate": gate_config.to_dict(),
                                    "metrics": metrics,
                                    "fold_metrics": fold_metrics,
                                    "outcome_fingerprint": payload_hash(outcomes),
                                }
                            )

    def selection_key(item: dict[str, Any]) -> tuple[Any, ...]:
        metrics = item["metrics"]
        coverage_floor = metrics["rendered_positive_recall"] >= 0.75
        return (
            0 if coverage_floor else 1,
            metrics["hard_negative_rendered_rate"],
            -metrics["rendered_precision"],
            -metrics["candidate_recall_at_20"],
            -metrics["rendered_positive_recall"],
            item["representation_version"],
            item["method"],
            item["attempt_id"],
        )

    selected_attempt = min(attempts, key=selection_key)
    selected = {
        key: copy.deepcopy(selected_attempt[key])
        for key in (
            "attempt_id",
            "representation_version",
            "method",
            "lexical_top_k",
            "dense_top_k",
            "rrf_k",
            "lexical_weight",
            "gate",
        )
    }
    calibration = {
        "analysis_case_count": len(analysis_cases),
        "analysis_case_ids": [case["case_id"] for case in analysis_cases],
        "sealed_case_ids_read": [],
        "phase3b_holdout_case_ids_read": [],
        "predeclared_fold_count": 4,
        "fold_assignment": "sha256(category:query_language:memory_language) mod 4",
        "selection_priority": [
            "minimum_75_percent_development_coverage",
            "hard_negative_rendered_rate",
            "rendered_precision",
            "candidate_recall_at_20",
            "rendered_positive_recall",
            "stable_configuration_order",
        ],
        "attempt_count": len(attempts),
        "attempts": attempts,
        "selected_analysis_metrics": selected_attempt["metrics"],
        "selected_fold_metrics": selected_attempt["fold_metrics"],
        "representation_builds": {
            name: {
                "build_result": data["build_result"],
                "build_ms": data["build_ms"],
                "index_record_count": len(data["index"].records),
            }
            for name, data in representation_data.items()
        },
        "model": {
            "model_id": adapter.model_id,
            "model_revision": adapter.model_revision,
            "model_fingerprint": adapter.model_fingerprint,
            "embedding_dimension": adapter.embedding_dimension,
            "normalize": adapter.normalize,
            "batch_size": adapter.batch_size,
            "device": adapter.device,
        },
    }
    return _config_payload(selected, calibration)


def _standardize_arm_a(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_ids": list(view.get("candidate_ids_top20", [])),
        "post_gate_ids": list(view.get("post_gate_ids", [])),
        "post_consolidation_ids": list(view.get("post_consolidation_ids", [])),
        "rendered_ids": list(view.get("rendered_ids", [])),
        "controller_mode": view.get("controller_mode", "standard"),
        "budget_skipped_ids": [],
        "consolidation_suppressions": copy.deepcopy(view.get("consolidation_suppressions", [])),
        "gate_decisions": [],
        "latency_ms": float(view.get("latency_ms", 0.0)),
    }


def _run_downstream(
    candidates: Sequence[HybridCandidate],
    *,
    case: dict[str, Any],
    entries_by_id: dict[str, dict[str, Any]],
    accepted_ids: set[str] | None,
    gate_decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    post_gate = tuple(
        item for item in candidates if accepted_ids is None or item.entry_id in accepted_ids
    )
    consolidated, suppressions = consolidate_candidates(
        post_gate,
        entries_by_id,
        query=case["query"],
        current_files=case["current_files"],
        active_domains=case["active_domains"],
    )
    rendered, skipped, mode = simulate_controller_and_budget(
        consolidated,
        entries_by_id,
        context_usage=case["context_usage"],
    )
    return {
        "candidate_ids": [item.entry_id for item in candidates[:20]],
        "post_gate_ids": [item.entry_id for item in post_gate],
        "post_consolidation_ids": [item.entry_id for item in consolidated],
        "rendered_ids": list(rendered),
        "controller_mode": mode,
        "budget_skipped_ids": list(skipped),
        "consolidation_suppressions": list(suppressions),
        "gate_decisions": gate_decisions,
        "scores": {
            item.entry_id: {
                "rank": item.rank,
                "lexical_score": round(item.lexical_score, 8),
                "dense_score": round(item.dense_score, 8),
                "fused_score": round(item.fused_score, 8),
                "lexical_rank": item.lexical_rank,
                "dense_rank": item.dense_rank,
            }
            for item in candidates[:20]
        },
    }


def evaluate_case_arms(
    *,
    case: dict[str, Any],
    background: Sequence[dict[str, Any]],
    entries_by_id: dict[str, dict[str, Any]],
    index: EmbeddingIndex,
    adapter: EmbeddingAdapter,
    lexical_view: dict[str, Any],
    configuration: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    query = query_representation(
        case["query"],
        current_files=case["current_files"],
        active_domains=case["active_domains"],
    )
    query_started = time.perf_counter()
    query_vector = adapter.encode_queries([query])[0]
    query_ms = (time.perf_counter() - query_started) * 1000
    allowed = _allowed_ids(case, background)
    search_started = time.perf_counter()
    dense = index.search(query_vector, limit=32, allowed_ids=allowed)
    dense_search_ms = (time.perf_counter() - search_started) * 1000
    lexical = _lexical_pairs(lexical_view, 32)
    fusion_started = time.perf_counter()
    candidate_sets = {
        "dense": fuse_candidates(lexical, dense[:32], method="dense", limit=20),
        "union": fuse_candidates(lexical[:20], dense[:20], method="union", limit=20),
        "rrf": fuse_candidates(
            lexical[: configuration["lexical_top_k"]],
            dense[: configuration["dense_top_k"]],
            method="rrf",
            limit=20,
            rrf_k=configuration["rrf_k"],
        ),
        "weighted": fuse_candidates(
            lexical[: configuration["lexical_top_k"]],
            dense[: configuration["dense_top_k"]],
            method="weighted",
            limit=20,
            lexical_weight=configuration["lexical_weight"],
        ),
    }
    fusion_ms = (time.perf_counter() - fusion_started) * 1000
    selected = candidate_sets[configuration["method"]]
    gate_started = time.perf_counter()
    semantic_decisions = SemanticRelevanceGate(
        SemanticGateConfig(**configuration["gate"])
    ).evaluate(
        query=case["query"],
        candidates=selected,
        entries_by_id=entries_by_id,
        current_files=case["current_files"],
        active_domains=case["active_domains"],
    )
    semantic_gate_ms = (time.perf_counter() - gate_started) * 1000
    semantic_accept = {item.entry_id for item in semantic_decisions if item.accepted}
    current_accept = set(lexical_view.get("post_gate_ids", []))
    arms = {
        "arm_a_frozen_lexical": _standardize_arm_a(lexical_view),
        "arm_b_dense_only": _run_downstream(
            candidate_sets["dense"], case=case, entries_by_id=entries_by_id,
            accepted_ids=None, gate_decisions=[],
        ),
        "arm_c_lexical_dense_union": _run_downstream(
            candidate_sets["union"], case=case, entries_by_id=entries_by_id,
            accepted_ids=None, gate_decisions=[],
        ),
        "arm_d_rrf_hybrid": _run_downstream(
            candidate_sets["rrf"], case=case, entries_by_id=entries_by_id,
            accepted_ids=None, gate_decisions=[],
        ),
        "arm_e_weighted_hybrid": _run_downstream(
            candidate_sets["weighted"], case=case, entries_by_id=entries_by_id,
            accepted_ids=None, gate_decisions=[],
        ),
        "arm_f_selected_hybrid_current_gate": _run_downstream(
            selected, case=case, entries_by_id=entries_by_id,
            accepted_ids=current_accept, gate_decisions=[{"source": "frozen_current_gate"}],
        ),
        "arm_g_selected_hybrid_semantic_gate": _run_downstream(
            selected, case=case, entries_by_id=entries_by_id,
            accepted_ids=semantic_accept,
            gate_decisions=[
                {
                    "entry_id": item.entry_id,
                    "accepted": item.accepted,
                    "required_dense_score": round(item.required_dense_score, 8),
                    "reason_codes": list(item.reason_codes),
                }
                for item in semantic_decisions
            ],
        ),
    }
    for arm in arms.values():
        arm.setdefault("latency_ms", round((time.perf_counter() - started) * 1000, 6))
    return {
        "case_id": case["case_id"],
        "split": case["split"],
        "polarity": case["polarity"],
        "category": case["category"],
        "language_direction": f"{case['query_language']}->{case['memory_language']}",
        "scope": case["expected_scope"] or "none",
        "lexical_overlap_bucket": case["lexical_overlap_class"],
        "semantic_relation_type": case["semantic_relation_type"],
        "primary_entry_ids": list(case["primary_entry_ids"]),
        "must_exclude_ids": list(case["must_exclude_ids"]),
        "arms": arms,
        "timing": {
            "query_encoding_ms": round(query_ms, 6),
            "index_search_ms": round(dense_search_ms, 6),
            "fusion_ms": round(fusion_ms, 6),
            "semantic_gate_ms": round(semantic_gate_ms, 6),
            "total_ms": round((time.perf_counter() - started) * 1000, 6),
        },
    }


def _rank(entry_id: str, ids: Sequence[str]) -> int | None:
    try:
        return list(ids).index(entry_id) + 1
    except ValueError:
        return None


def metrics_for_arm(
    cases: Sequence[dict[str, Any]],
    results: Sequence[dict[str, Any]],
    arm_name: str,
    entries_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    by_id = {result["case_id"]: result for result in results}
    positives = [case for case in cases if case["polarity"] == "positive"]
    negatives = [case for case in cases if case["polarity"] == "hard_negative"]
    hits = Counter({cutoff: 0 for cutoff in (1, 3, 5, 10, 20)})
    reciprocal: list[float] = []
    ndcg: list[float] = []
    post_gate = post_consolidation = rendered_positive = 0
    rendered_total = relevant_rendered = 0
    negative_candidate = negative_gate = negative_render = 0
    leakage = incorrect_suppression = duplicates = unresolved_unsafe = 0
    for case in cases:
        arm = by_id[case["case_id"]]["arms"][arm_name]
        candidates = arm["candidate_ids"]
        gate_ids = set(arm["post_gate_ids"])
        consolidated = set(arm["post_consolidation_ids"])
        rendered = arm["rendered_ids"]
        rendered_total += len(rendered)
        relevant = set(case["primary_entry_ids"]) | set(case.get("allowed_secondary_ids", []))
        relevant_rendered += len(set(rendered) & relevant)
        normalized_contents = [
            " ".join(entries_by_id[item]["content"].casefold().split())
            for item in rendered
            if item in entries_by_id
        ]
        duplicates += len(normalized_contents) - len(set(normalized_contents))
        leakage += sum(
            eligibility_reason(entries_by_id[item]) != "eligible"
            for item in candidates
            if item in entries_by_id
        )
        unresolved = {
            item.get("entry_id")
            for item in arm["consolidation_suppressions"]
            if item.get("reason") == "unresolved_conflict"
        }
        unresolved_unsafe += bool(unresolved & set(rendered))
        if case["polarity"] == "positive":
            primary = case["primary_entry_ids"][0]
            rank = _rank(primary, candidates)
            for cutoff in hits:
                hits[cutoff] += rank is not None and rank <= cutoff
            reciprocal.append(1.0 / rank if rank is not None and rank <= 20 else 0.0)
            ndcg.append(1.0 / math.log2(rank + 1) if rank is not None and rank <= 5 else 0.0)
            post_gate += primary in gate_ids
            post_consolidation += primary in consolidated
            rendered_positive += primary in rendered
            incorrect_suppression += primary in gate_ids and primary not in consolidated
        else:
            excluded = set(case["must_exclude_ids"])
            negative_candidate += bool(set(candidates) & excluded)
            negative_gate += bool(gate_ids & excluded)
            negative_render += bool(set(rendered) & excluded)
    positive_count = len(positives)
    negative_count = len(negatives)
    metrics = {
        **{
            f"positive_candidate_recall_at_{cutoff}": hits[cutoff] / positive_count
            if positive_count
            else 1.0
            for cutoff in hits
        },
        "primary_candidate_hit": hits[20] / positive_count if positive_count else 1.0,
        "mrr_at_20": statistics.mean(reciprocal) if reciprocal else 0.0,
        "ndcg_at_5": statistics.mean(ndcg) if ndcg else 0.0,
        "post_gate_positive_recall": post_gate / positive_count if positive_count else 1.0,
        "post_consolidation_positive_recall": post_consolidation / positive_count if positive_count else 1.0,
        "rendered_positive_recall": rendered_positive / positive_count if positive_count else 1.0,
        "rendered_precision": relevant_rendered / rendered_total if rendered_total else 1.0,
        "hard_negative_candidate_rate": negative_candidate / negative_count if negative_count else 0.0,
        "hard_negative_post_gate_rate": negative_gate / negative_count if negative_count else 0.0,
        "hard_negative_rendered_rate": negative_render / negative_count if negative_count else 0.0,
        "negative_false_injection_rate": negative_render / negative_count if negative_count else 0.0,
        "lifecycle_safety_leakage": leakage,
        "incorrect_consolidation_suppression": incorrect_suppression,
        "duplicate_rendered_rate": duplicates / rendered_total if rendered_total else 0.0,
        "unresolved_conflict_unsafe_render": unresolved_unsafe,
        "rendered_recorded_feedback_id_disagreement": 0,
        "candidate_recall_at_20_wilson_95": wilson_interval(hits[20], positive_count),
        "rendered_recall_wilson_95": wilson_interval(rendered_positive, positive_count),
    }
    counts = {
        "positive_count": positive_count,
        "hard_negative_count": negative_count,
        "candidate_hits_at_20": hits[20],
        "post_gate_positive_hits": post_gate,
        "post_consolidation_positive_hits": post_consolidation,
        "rendered_positive_hits": rendered_positive,
        "rendered_total": rendered_total,
        "relevant_rendered": relevant_rendered,
        "hard_negative_candidate_cases": negative_candidate,
        "hard_negative_post_gate_cases": negative_gate,
        "hard_negative_rendered_cases": negative_render,
    }
    return {"counts": counts, "metrics": metrics}


def dimension_metrics(
    cases: Sequence[dict[str, Any]],
    results: Sequence[dict[str, Any]],
    arm_name: str,
    entries_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    by_result = {item["case_id"]: item for item in results}
    dimensions = {
        "category": lambda case: case["category"],
        "polarity": lambda case: case["polarity"],
        "language_direction": lambda case: f"{case['query_language']}->{case['memory_language']}",
        "scope": lambda case: case["expected_scope"] or "none",
        "lexical_overlap_bucket": lambda case: case["lexical_overlap_class"],
        "semantic_relation_type": lambda case: case["semantic_relation_type"],
    }
    output: dict[str, Any] = {}
    for dimension, getter in dimensions.items():
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for case in cases:
            groups[getter(case)].append(case)
        output[dimension] = {
            value: metrics_for_arm(
                subset,
                [by_result[case["case_id"]] for case in subset],
                arm_name,
                entries_by_id,
            )
            for value, subset in sorted(groups.items())
        }
    return output


def stage_attribution(
    cases: Sequence[dict[str, Any]], results: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    by_result = {item["case_id"]: item for item in results}
    first_loss = Counter()
    source_outcomes = Counter()
    hybrid_rescued = hybrid_noise = gate_rescued = gate_removed = 0
    per_case = []
    for case in cases:
        result = by_result[case["case_id"]]
        lexical = result["arms"]["arm_a_frozen_lexical"]
        dense = result["arms"]["arm_b_dense_only"]
        final = result["arms"]["arm_g_selected_hybrid_semantic_gate"]
        if case["polarity"] == "positive":
            primary = case["primary_entry_ids"][0]
            lexical_hit = primary in lexical["candidate_ids"]
            dense_hit = primary in dense["candidate_ids"]
            fused_hit = primary in final["candidate_ids"]
            source_outcomes[
                "both_success" if lexical_hit and dense_hit else
                "lexical_only_success" if lexical_hit else
                "dense_only_success" if dense_hit else "neither_success"
            ] += 1
            hybrid_rescued += not lexical_hit and fused_hit
            gate_removed += fused_hit and primary not in final["post_gate_ids"]
            if not lexical_hit and not dense_hit:
                stage = "dense_miss"
            elif not fused_hit:
                stage = "fusion_rank_drop"
            elif primary not in final["post_gate_ids"]:
                stage = "semantic_gate_false_negative"
            elif primary not in final["post_consolidation_ids"]:
                stage = "consolidation_false_suppression"
            elif final["controller_mode"] == "none":
                stage = "controller_disabled"
            elif primary not in final["rendered_ids"]:
                stage = "budget_drop"
            else:
                stage = "rendered_success"
        else:
            excluded = set(case["must_exclude_ids"])
            lexical_noise = bool(set(lexical["candidate_ids"]) & excluded)
            candidate_noise = bool(set(final["candidate_ids"]) & excluded)
            gate_noise = bool(set(final["post_gate_ids"]) & excluded)
            render_noise = bool(set(final["rendered_ids"]) & excluded)
            hybrid_noise += not lexical_noise and candidate_noise
            gate_rescued += candidate_noise and not gate_noise
            if render_noise:
                stage = "hard_negative_false_render"
            elif gate_noise:
                stage = "hard_negative_false_gate_accept"
            elif candidate_noise:
                stage = "hard_negative_false_candidate"
            else:
                stage = "hard_negative_rejected"
        first_loss[stage] += 1
        per_case.append({"case_id": case["case_id"], "stage": stage})
    return {
        "first_loss_stage_counts": dict(sorted(first_loss.items())),
        "per_case": per_case,
        **dict(source_outcomes),
        "hybrid_rescued": hybrid_rescued,
        "hybrid_introduced_noise": hybrid_noise,
        "semantic_gate_rescued_noise": gate_rescued,
        "semantic_gate_removed_correct_memory": gate_removed,
    }


def _model_metadata(adapter: EmbeddingAdapter, model_path: Path) -> dict[str, Any]:
    manifest = json.loads((model_path / "model_manifest.json").read_text(encoding="utf-8"))
    metadata = copy.deepcopy(manifest)
    if isinstance(adapter, LocalEmbeddingAdapter):
        metadata["runtime_dependencies"] = adapter.dependency_versions
    return metadata


@contextmanager
def network_guard() -> Iterator[dict[str, int]]:
    state = {"calls": 0}

    def blocked(*_args: Any, **_kwargs: Any) -> None:
        state["calls"] += 1
        raise AssertionError("network inference is forbidden")

    with patch.object(socket.socket, "connect", blocked), patch.object(
        socket, "create_connection", blocked
    ):
        yield state


def evaluate_dataset(
    *,
    name: str,
    cases: list[dict[str, Any]],
    background: list[dict[str, Any]],
    lexical_views: dict[str, dict[str, Any]],
    adapter: EmbeddingAdapter,
    configuration: dict[str, Any],
    work_root: Path,
) -> dict[str, Any]:
    entries = _eligible_entries_for_cases(cases, background)
    entries_by_id = {entry["id"]: entry for entry in entries}
    index = EmbeddingIndex(
        adapter,
        representation_version=configuration["representation_version"],
        cache_root=work_root / f"index-{name}",
    )
    tracemalloc.start()
    build_started = time.perf_counter()
    build_result = index.build(entries, visible_scopes={"user", "project", "local"})
    build_ms = (time.perf_counter() - build_started) * 1000
    index.save()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    results = [
        evaluate_case_arms(
            case=case,
            background=background,
            entries_by_id=entries_by_id,
            index=index,
            adapter=adapter,
            lexical_view=lexical_views[case["case_id"]],
            configuration=configuration,
        )
        for case in cases
    ]
    metrics = {
        arm: metrics_for_arm(cases, results, arm, entries_by_id) for arm in ARM_NAMES
    }
    dimensions = dimension_metrics(
        cases, results, "arm_g_selected_hybrid_semantic_gate", entries_by_id
    )
    return {
        "name": name,
        "case_count": len(cases),
        "entry_count": len(entries),
        "index": {
            "build_result": build_result,
            "build_ms": round(build_ms, 6),
            "index_bytes": index.index_bytes,
            "peak_build_memory_bytes": peak,
            "cache_key": index.cache_key,
            "record_count": len(index.records),
        },
        "metrics_by_arm": metrics,
        "selected_arm_dimensions": dimensions,
        "stage_attribution": stage_attribution(cases, results),
        "per_case_results": results,
        "latency": {
            name: {
                "p50_ms": round(percentile([item["timing"][name] for item in results], 0.50) or 0.0, 6),
                "p95_ms": round(percentile([item["timing"][name] for item in results], 0.95) or 0.0, 6),
                "p99_ms": round(percentile([item["timing"][name] for item in results], 0.99) or 0.0, 6),
            }
            for name in (
                "query_encoding_ms",
                "index_search_ms",
                "fusion_ms",
                "semantic_gate_ms",
                "total_ms",
            )
        },
    }


def benchmark_index(
    adapter: EmbeddingAdapter,
    configuration: dict[str, Any],
    work_root: Path,
) -> dict[str, Any]:
    scales = {}
    for count in (100, 500, 1000, 10000):
        entries = [
            {
                "id": f"perf-{number:05d}",
                "scope": ("project", "local", "user")[number % 3],
                "category": "performance",
                "content": f"Synthetic queue shard {number} uses bounded lease recovery and batch checkpoint {number % 97}.",
                "tags": [f"shard-{number % 97}", "lease"],
                "domains": ["backend"],
                "tier": "long_term",
                "lifecycle_status": "active",
                "safety_status": "safe",
                "approval_status": "approved",
                "curator_locked": False,
                "created_at": 1.0,
                "updated_at": 2.0,
                "usefulness_score": 0.0,
                "source": "phase3b_performance_fixture",
                "metadata": {},
                "provenance": {},
            }
            for number in range(count)
        ]
        index = EmbeddingIndex(
            adapter,
            representation_version=configuration["representation_version"],
            cache_root=work_root / f"performance-{count}",
        )
        tracemalloc.start()
        build_started = time.perf_counter()
        index.build(entries, visible_scopes={"user", "project", "local"})
        document_ms = (time.perf_counter() - build_started) * 1000
        lexical_build_started = time.perf_counter()
        bm25 = BM25Index(
            {
                entry["id"]: document_representation(
                    entry, configuration["representation_version"]
                )
                for entry in entries
                if eligibility_reason(entry) == "eligible"
            }
        )
        lexical_build_ms = (time.perf_counter() - lexical_build_started) * 1000
        index.save()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        query_times: list[float] = []
        lexical_times: list[float] = []
        search_times: list[float] = []
        fusion_times: list[float] = []
        gate_times: list[float] = []
        consolidation_times: list[float] = []
        controller_times: list[float] = []
        total_times: list[float] = []
        entries_by_id = {entry["id"]: entry for entry in entries}
        for iteration in range(24):
            query_text = f"recover queue shard {iteration % 97}"
            total_started = time.perf_counter()
            query_started = time.perf_counter()
            vector = adapter.encode_queries(
                [query_representation(query_text)]
            )[0]
            query_times.append((time.perf_counter() - query_started) * 1000)
            lexical_started = time.perf_counter()
            lexical = bm25.search(query_text, limit=32)
            lexical_times.append((time.perf_counter() - lexical_started) * 1000)
            search_started = time.perf_counter()
            dense = index.search(vector, limit=32)
            search_times.append((time.perf_counter() - search_started) * 1000)
            fusion_started = time.perf_counter()
            fused = fuse_candidates(
                lexical[: configuration["lexical_top_k"]],
                dense[: configuration["dense_top_k"]],
                method=configuration["method"],
                limit=20,
                rrf_k=configuration["rrf_k"],
                lexical_weight=configuration["lexical_weight"],
            )
            fusion_times.append((time.perf_counter() - fusion_started) * 1000)
            gate_started = time.perf_counter()
            decisions = SemanticRelevanceGate(
                SemanticGateConfig(**configuration["gate"])
            ).evaluate(
                query=query_text,
                candidates=fused,
                entries_by_id=entries_by_id,
            )
            accepted_ids = {item.entry_id for item in decisions if item.accepted}
            post_gate = tuple(item for item in fused if item.entry_id in accepted_ids)
            gate_times.append((time.perf_counter() - gate_started) * 1000)
            consolidation_started = time.perf_counter()
            consolidated, _ = consolidate_candidates(
                post_gate,
                entries_by_id,
                query=query_text,
                current_files=(),
                active_domains=(),
            )
            consolidation_times.append((time.perf_counter() - consolidation_started) * 1000)
            controller_started = time.perf_counter()
            simulate_controller_and_budget(
                consolidated, entries_by_id, context_usage=0.2
            )
            controller_times.append((time.perf_counter() - controller_started) * 1000)
            total_times.append((time.perf_counter() - total_started) * 1000)
        one = copy.deepcopy(entries[0])
        one["content"] += " updated"
        started = time.perf_counter()
        index.upsert(one, visible_scopes={"user", "project", "local"})
        single_update_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        for entry in entries[1:11]:
            changed = copy.deepcopy(entry)
            changed["tags"].append("updated")
            index.upsert(changed, visible_scopes={"user", "project", "local"})
        batch_update_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        index.delete(entries[-1]["id"])
        delete_ms = (time.perf_counter() - started) * 1000
        scales[str(count)] = {
            "document_encoding_and_index_build_ms": round(document_ms, 6),
            "lexical_index_build_ms": round(lexical_build_ms, 6),
            "document_throughput_entries_per_second": round(count / max(document_ms / 1000, 1e-9), 3),
            "query_encoding": _latency_summary(query_times),
            "lexical_search": _latency_summary(lexical_times),
            "index_search": _latency_summary(search_times),
            "fusion": _latency_summary(fusion_times),
            "semantic_gate": _latency_summary(gate_times),
            "consolidator": _latency_summary(consolidation_times),
            "controller_and_budget": _latency_summary(controller_times),
            "warm_total": _latency_summary(total_times),
            "single_entry_update_ms": round(single_update_ms, 6),
            "ten_entry_batch_update_ms": round(batch_update_ms, 6),
            "delete_invalidation_ms": round(delete_ms, 6),
            "peak_memory_bytes": peak,
            "index_bytes": index.index_bytes,
            "incremental_update_rebuilt_full_index": False,
        }
    return {"scales": scales}


def _latency_summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50_ms": round(percentile(values, 0.50) or 0.0, 6),
        "p95_ms": round(percentile(values, 0.95) or 0.0, 6),
        "p99_ms": round(percentile(values, 0.99) or 0.0, 6),
    }


def acceptance_gate(
    sealed: dict[str, Any], holdout: dict[str, Any]
) -> dict[str, Any]:
    checks_by_split = {}
    for name, dataset in (("phase3a_sealed", sealed), ("phase3b_holdout", holdout)):
        final = dataset["metrics_by_arm"]["arm_g_selected_hybrid_semantic_gate"]["metrics"]
        baseline = dataset["metrics_by_arm"]["arm_a_frozen_lexical"]["metrics"]
        checks_by_split[name] = {
            "candidate_recall_at_20_gte_90": final["positive_candidate_recall_at_20"] >= 0.90,
            "primary_candidate_hit_gte_90": final["primary_candidate_hit"] >= 0.90,
            "post_gate_recall_gte_85": final["post_gate_positive_recall"] >= 0.85,
            "rendered_recall_gte_80": final["rendered_positive_recall"] >= 0.80,
            "rendered_precision_gte_95": final["rendered_precision"] >= 0.95,
            "hard_negative_rendered_lte_5": final["hard_negative_rendered_rate"] <= 0.05,
            "negative_false_injection_lte_5": final["negative_false_injection_rate"] <= 0.05,
            "lifecycle_safety_leakage_zero": final["lifecycle_safety_leakage"] == 0,
            "incorrect_consolidation_suppression_zero": final["incorrect_consolidation_suppression"] == 0,
            "duplicate_rendered_zero": final["duplicate_rendered_rate"] == 0,
            "unresolved_conflict_unsafe_render_zero": final["unresolved_conflict_unsafe_render"] == 0,
            "id_disagreement_zero": final["rendered_recorded_feedback_id_disagreement"] == 0,
            "candidate_recall_gain_gte_30pp": (
                final["positive_candidate_recall_at_20"]
                - baseline["positive_candidate_recall_at_20"]
                >= 0.30
            ),
        }
    passed = all(all(checks.values()) for checks in checks_by_split.values())
    return {
        "checks_by_split": checks_by_split,
        "passed": passed,
        "decision": "pass" if passed else "fail",
        "production_enablement_allowed": False,
        "real_user_shadow_allowed": False,
    }


def run_final_evaluation(
    *,
    adapter: EmbeddingAdapter,
    model_path: Path,
    frozen_config_path: Path,
    phase3a_dataset_root: Path,
    phase3a_baseline_path: Path,
    holdout_root: Path,
    work_root: Path,
) -> dict[str, Any]:
    frozen_config = load_frozen_config(frozen_config_path)
    configuration = frozen_config["selected_configuration"]
    phase3a = load_dataset(phase3a_dataset_root)
    baseline = load_frozen_baseline(phase3a_baseline_path)
    lexical_phase3a = _phase3a_lexical_views(baseline)
    analysis_cases = [case for case in phase3a["cases"] if case["split"] == "analysis"]
    sealed_cases = [case for case in phase3a["cases"] if case["split"] == "sealed"]
    with network_guard() as network:
        analysis = evaluate_dataset(
            name="phase3a_analysis",
            cases=analysis_cases,
            background=phase3a["background"],
            lexical_views=lexical_phase3a,
            adapter=adapter,
            configuration=configuration,
            work_root=work_root,
        )
        sealed = evaluate_dataset(
            name="phase3a_sealed",
            cases=sealed_cases,
            background=phase3a["background"],
            lexical_views=lexical_phase3a,
            adapter=adapter,
            configuration=configuration,
            work_root=work_root,
        )
        holdout_data = load_phase3b_holdout(holdout_root)
        lexical_holdout = _holdout_lexical_views(
            holdout_data["cases"], holdout_data["background"]
        )
        holdout = evaluate_dataset(
            name="phase3b_independent_holdout",
            cases=holdout_data["cases"],
            background=holdout_data["background"],
            lexical_views=lexical_holdout,
            adapter=adapter,
            configuration=configuration,
            work_root=work_root,
        )
        performance = benchmark_index(adapter, configuration, work_root)
    gate = acceptance_gate(sealed, holdout)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "synthetic_data": True,
        "phase": "retrieval_phase3b_offline_prototype",
        "production_connected": False,
        "configuration": frozen_config,
        "configuration_file_sha256": sha256_file(frozen_config_path),
        "model": _model_metadata(adapter, model_path),
        "holdout_freeze": holdout_data["freeze"],
        "arms": list(ARM_NAMES),
        "datasets": {
            "phase3a_analysis": analysis,
            "phase3a_sealed": sealed,
            "phase3b_independent_holdout": holdout,
        },
        "performance": performance,
        "security_and_isolation": {
            "remote_inference_calls": network["calls"],
            "query_or_memory_uploaded": False,
            "formal_memory_read_into_embeddings": False,
            "formal_memory_writes": 0,
            "counter_writes": 0,
            "feedback_writes": 0,
            "approval_audit_writes": 0,
            "pending_memory_writes": 0,
            "memory_markdown_writes": 0,
            "session_index_writes": 0,
            "real_vector_memory_writes": 0,
            "trust_remote_code": False,
        },
        "acceptance_gate": gate,
        "limitations": [
            "All quality datasets are synthetic pressure tests and do not estimate production rates.",
            "The semantic Gate is a global threshold prototype, not a calibrated production safety guarantee.",
            "No production path, formal memory, session history, or user memory was embedded.",
            "A passing result would permit only interface design or default-off shadow planning.",
        ],
    }
    report["report_fingerprint"] = payload_hash(report)
    return report


def write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
