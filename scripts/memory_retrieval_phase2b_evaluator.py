"""Offline evaluator for deterministic post-gate candidate consolidation."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import socket
import statistics
import tempfile
import time
import tracemalloc
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from scripts.memory_retrieval_evaluator import load_dataset
from scripts.memory_retrieval_phase2a_evaluator import evaluate_phase2a_dataset


PHASE2B_EVALUATOR_VERSION = "1.1.0"
REFERENCE_TIME = 1736942400
PHASE2A_CANONICAL_P95_MS = 2.1233
PHASE2A_P95_MATERIAL_LIMIT_MS = PHASE2A_CANONICAL_P95_MS * 1.35
PERFORMANCE_ENFORCEMENT_MODES = frozenset({"advisory", "strict"})
PHASE1_FROZEN_HASHES = {
    "artifacts/memory-retrieval-baseline.json": "862050500ad75d8d4ca248dddd34cd6d7ff00d71159e4774f20e300766801ae7",
    "docs/memory-retrieval-baseline.md": "f9f072ab3a90095429ace0f6482050711b532722a84d06d8667158c94dffcd3a",
    "docs/memory-retrieval-chain-audit.md": "eb75c12a6ebc9c7336ce407c6c5d3928f9ded9dc037de868f6e6c3a64837855e",
    "tests/fixtures/memory_retrieval_golden/README.md": "ba374e67858b475d7dc423eddcfd0fc87a77e654e293d84c79cfb3932dd1b651",
    "tests/fixtures/memory_retrieval_golden/schema.json": "aa0781e50fd225c4f772e540bf24a7221ac7a2d2ae88db57d9e9101ec85cf7be",
    "tests/fixtures/memory_retrieval_golden/cases/01_exact_lexical.json": "3cb8831ea3c4e5d9230f94c545ba07e7d64fbc93e9a6429fa855f31a852839a7",
    "tests/fixtures/memory_retrieval_golden/cases/02_paraphrase_synonym.json": "a2b4b18855acbc8b9feb0a3cbddcffcc50325b47bec34ca3dc0dee133c6d03bc",
    "tests/fixtures/memory_retrieval_golden/cases/03_multilingual.json": "5017b5b980b680f527c95a9f6897ebf156d477476e9f7dc292fa6c8d976f7c04",
    "tests/fixtures/memory_retrieval_golden/cases/04_file_domain_context.json": "c92690874d4bd01ed035155b2db08b1944b000c10e57d41bdb425005ea5b2f8d",
    "tests/fixtures/memory_retrieval_golden/cases/05_cross_scope_ranking.json": "bbff8f0e99fe2934f018cd485df15c30d6ecbe7601c718d5f5700999a15c5c80",
    "tests/fixtures/memory_retrieval_golden/cases/06_lifecycle_safety.json": "d35a86a360a2bafa3dc367236a1daf981d67661c1b25b7726365df5cdd0d7b0c",
    "tests/fixtures/memory_retrieval_golden/cases/07_negative_no_match.json": "43b93c6d9efcdc24649f9486b89d0a75c0943e8628e47af7f12e1b9a39f56091",
    "tests/fixtures/memory_retrieval_golden/cases/08_duplicate_conflict_budget.json": "3d576a9e5a57a92dd7e4ceefd05f29f7a6a4a552f2909c52e175d31f625428bf",
    "tests/fixtures/memory_retrieval_golden/cases/09_failure_recovery_correction.json": "2f70276d6fe61a8a4263ba15f2ba31742c693acfae7684d6712ce032c74474bc",
    "tests/fixtures/memory_retrieval_golden/cases/10_entrypoint_consistency.json": "0507f679f3de1ad30178d3e82cdb84408db3fc3d676ef49c2a569fb77e31d195",
}
PHASE2A_FROZEN_HASHES = {
    "artifacts/memory-retrieval-phase2a.json": "2f488120e4016d9fafb275cd2b22b7e978ddf8f4039b990aeff1724e00759327",
    "docs/memory-retrieval-phase2a-comparison.md": "4c148cbe54f4e3d39ed5f2e1726f8ba7ee465b93d9329d7f39d884c0fa66e3fe",
    "docs/memory-retrieval-phase2a.md": "7414300118d678bbf7d1e1c9eba91c473d11044b83fc19d4ebc7f705d702b09b",
    "scripts/evaluate_memory_retrieval_phase2a.py": "24caf504c1b7965cb4ad69e539091a7d741eb4f0a00b9903d1d6a289a48185b5",
    "scripts/memory_retrieval_evaluator.py": "70178d0bda4f705ff59ecb31602179cdb1f3901896aa688f00d95ddf88701389",
    "scripts/memory_retrieval_phase2a_evaluator.py": "e65b6ecb59804d7ff5aa04113f6028b64d546c2abf75436175dc40bf39c4a404",
    "tests/test_memory_retrieval_phase2a.py": "f5ec44edf9cac7191fc0960dec5992814899864a4fdeb4600dcfcef5fdd25f6f",
    "tests/test_memory_retrieval_phase2a_evaluator.py": "bb8193c5c60b4025f96908251c0af8594764dff66c6c80d48e7e780fb4748759",
}


def _require_nonnegative_finite_number(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{name} must be a finite non-negative number")
    return float(value)


def _require_nonnegative_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def evaluate_performance_policy(
    *,
    canonical_p95_ms: float,
    consolidator_100_p95_ms: float,
    retained_count_500: int,
    retained_count_1000: int,
    network_call_count: int,
    enforcement_mode: str,
) -> dict[str, Any]:
    """Classify measured performance without collecting or mutating anything."""
    if (
        not isinstance(enforcement_mode, str)
        or enforcement_mode not in PERFORMANCE_ENFORCEMENT_MODES
    ):
        raise ValueError(f"unsupported enforcement_mode: {enforcement_mode!r}")
    canonical_p95_ms = _require_nonnegative_finite_number(
        "canonical_p95_ms", canonical_p95_ms
    )
    consolidator_100_p95_ms = _require_nonnegative_finite_number(
        "consolidator_100_p95_ms", consolidator_100_p95_ms
    )
    retained_count_500 = _require_nonnegative_integer(
        "retained_count_500", retained_count_500
    )
    retained_count_1000 = _require_nonnegative_integer(
        "retained_count_1000", retained_count_1000
    )
    network_call_count = _require_nonnegative_integer(
        "network_call_count", network_call_count
    )
    deterministic_gates = {
        "no_network_calls": network_call_count == 0,
        "candidate_cap_enforced_at_500": retained_count_500 <= 256,
        "candidate_cap_enforced_at_1000": retained_count_1000 <= 256,
    }
    wall_clock_gates = {
        "consolidator_100_p95_at_most_10_ms": consolidator_100_p95_ms <= 10.0,
        "canonical_p95_not_materially_above_phase2a": (
            canonical_p95_ms <= PHASE2A_P95_MATERIAL_LIMIT_MS
        ),
    }
    deterministic_passed = all(deterministic_gates.values())
    strict_passed = all(wall_clock_gates.values())
    return {
        "enforcementMode": enforcement_mode,
        "deterministicPassed": deterministic_passed,
        "strictPassed": strict_passed,
        "enforcementPassed": (
            deterministic_passed
            and (enforcement_mode == "advisory" or strict_passed)
        ),
        "deterministicGates": deterministic_gates,
        "wallClockGates": wall_clock_gates,
        "gates": {**wall_clock_gates, **deterministic_gates},
    }


def phase2b_exit_code(report: object) -> int:
    """Return the CLI status for a complete Phase 2B report, failing closed."""
    if not isinstance(report, dict) or report.get("acceptance_passed") is not True:
        return 1
    performance = report.get("performance")
    if not isinstance(performance, dict):
        return 1
    mode = performance.get("enforcementMode")
    deterministic_passed = performance.get("deterministicPassed")
    strict_passed = performance.get("strictPassed")
    if (
        mode not in PERFORMANCE_ENFORCEMENT_MODES
        or deterministic_passed is not True
        or not isinstance(strict_passed, bool)
    ):
        return 1
    if mode == "strict" and strict_passed is not True:
        return 1
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_paths(root: Path, expected: dict[str, str]) -> dict[str, Any]:
    actual = {
        relative: _sha256(root / relative) if (root / relative).is_file() else "missing"
        for relative in expected
    }
    mismatches = {
        relative: {"expected": expected[relative], "actual": actual[relative]}
        for relative in expected
        if actual[relative] != expected[relative]
    }
    return {
        "file_count": len(expected),
        "matches": not mismatches,
        "mismatches": mismatches,
        "actual_hashes": actual,
    }


def snapshot_tree(root: Path) -> dict[str, Any]:
    """Hash a tree without importing or writing any MiniCode state."""
    files: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            stat = path.stat()
            files.append(
                {
                    "path": str(path.relative_to(root)),
                    "sha256": _sha256(path),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
    return {"root": str(root), "file_count": len(files), "files": files}


def _tree_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    files = snapshot["files"]
    manifest = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "file_count": snapshot["file_count"],
        "total_size": sum(item["size"] for item in files),
        "manifest_sha256": hashlib.sha256(manifest.encode("utf-8")).hexdigest(),
        "mtime_min_ns": min((item["mtime_ns"] for item in files), default=None),
        "mtime_max_ns": max((item["mtime_ns"] for item in files), default=None),
    }


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def load_holdout(path: Path) -> list[dict[str, Any]]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") != "1.0" or document.get("synthetic_data") is not True:
        raise ValueError("unsupported or non-synthetic Phase 2B holdout")
    if document.get("reference_time") != REFERENCE_TIME:
        raise ValueError("Phase 2B holdout must use the fixed reference time")
    cases = document.get("cases")
    if not isinstance(cases, list) or len(cases) < 30:
        raise ValueError("Phase 2B holdout requires at least 30 cases")
    case_ids = [case.get("case_id") for case in cases]
    memory_ids = [memory.get("id") for case in cases for memory in case.get("memories", [])]
    if len(case_ids) != len(set(case_ids)) or len(memory_ids) != len(set(memory_ids)):
        raise ValueError("Phase 2B case and memory IDs must be globally unique")
    return sorted(copy.deepcopy(cases), key=lambda case: case["case_id"])


class _InstrumentedManagerMixin:
    def _init_observation(self) -> None:
        self.save_events: list[str] = []
        self.retrieval_calls: list[list[str]] = []
        self.injection_calls: list[list[str]] = []
        self.feedback_calls: list[dict[str, Any]] = []

    def _save_scope(self, scope: Any) -> None:
        self.save_events.append(scope.value)
        super()._save_scope(scope)

    def record_retrievals_and_injections(
        self,
        retrieved_entry_ids: list[str],
        injected_entry_ids: list[str],
    ) -> None:
        self.retrieval_calls.append(list(retrieved_entry_ids))
        self.injection_calls.append(list(injected_entry_ids))
        super().record_retrievals_and_injections(
            retrieved_entry_ids,
            injected_entry_ids,
        )

    def record_feedback(self, entry_ids: list[str], success: bool) -> None:
        self.feedback_calls.append({"entry_ids": list(entry_ids), "success": success})
        super().record_feedback(entry_ids, success)


@contextmanager
def _isolated_pipeline(case: dict[str, Any]) -> Iterator[tuple[Any, Any]]:
    from minicode import memory as memory_module
    from minicode.memory import MemoryEntry, MemoryManager, MemoryScope, MemoryTier
    from minicode.memory_pipeline import MemoryPipeline

    class InstrumentedMemoryManager(_InstrumentedManagerMixin, MemoryManager):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._init_observation()
            super().__init__(*args, **kwargs)

    with tempfile.TemporaryDirectory(prefix="minicode-phase2b-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        with patch.object(memory_module, "MINI_CODE_DIR", root / "home" / ".mini-code"):
            manager = InstrumentedMemoryManager(project_root=workspace)
        for memory in case["memories"]:
            entry = MemoryEntry(
                id=memory["id"],
                scope=MemoryScope(memory["scope"]),
                category=memory["category"],
                content=memory["content"],
                created_at=REFERENCE_TIME - 86400,
                updated_at=REFERENCE_TIME - 3600,
                tags=list(memory["tags"]),
                usage_count=memory.get("usage_count", 0),
                domains=list(memory["domains"]),
                tier=MemoryTier.LONG_TERM,
                last_accessed=REFERENCE_TIME - 3600,
                related_to=list(memory.get("related_to", [])),
                metadata=copy.deepcopy(memory.get("metadata", {})),
                provenance=copy.deepcopy(memory.get("provenance", {})),
                usefulness_score=memory.get("usefulness_score", 0.0),
            )
            manager.memories[entry.scope].entries.append(entry)
        for memory_file in manager.memories.values():
            memory_file._rebuild_indices()
        manager.workspace_path = workspace
        pipeline = MemoryPipeline(manager)
        pipeline.initialize(
            model_adapter=None,
            workspace_path=str(workspace),
            enable_reranker=False,
            enable_vector=False,
        )
        if pipeline._injector is not None:
            pipeline._injector._injection_cooldown = 0.0
        yield manager, pipeline


def _evaluate_holdout_case(case: dict[str, Any]) -> dict[str, Any]:
    from minicode.context_manager import estimate_tokens

    started = time.perf_counter()
    with _isolated_pipeline(case) as (manager, pipeline), patch(
        "minicode.memory.time.time", return_value=REFERENCE_TIME
    ), patch("minicode.memory_pipeline.time.time", return_value=REFERENCE_TIME), patch(
        "minicode.memory_injector.time.time", return_value=REFERENCE_TIME
    ), patch("minicode.memory_retrieval.time.time", return_value=REFERENCE_TIME):
        messages = [{"role": "system", "content": "SYSTEM_BASELINE"}]
        pipeline.inject(
            case["query"],
            case["current_files"],
            messages,
            context_usage=case["context_usage"],
            active_domains=case["active_domains"],
            max_memories=case["max_memories"],
            max_total_tokens=case["max_tokens"],
            max_tokens_per_memory=max(1, case["max_tokens"] // case["max_memories"]),
            min_relevance=0.0,
        )
        result = pipeline.last_retrieval_result
        task_start_saves = len(manager.save_events)
        pipeline.feedback(True)
        feedback_saves = len(manager.save_events) - task_start_saves
        if result is None:
            raise AssertionError(f"{case['case_id']}: pipeline produced no retrieval result")
        suppressions = [
            {
                "entry_id": item["entry_id"],
                "reason": item["reason"],
                "dominating_id": item["dominating_candidate_id"],
                "reason_codes": list(item["reason_codes"]),
                "chain_key": item["chain_key"],
            }
            for item in result.diagnostics.get("consolidation_suppressions", [])
        ]
        recorded_ids = [entry_id for call in manager.injection_calls for entry_id in call]
        feedback_ids = [
            entry_id for call in manager.feedback_calls for entry_id in call["entry_ids"]
        ]
        return {
            "case_id": case["case_id"],
            "category": case["category"],
            "candidate_ids": list(result.candidate_ids),
            "post_gate_ids": list(result.diagnostics.get("post_gate_ids", [])),
            "post_consolidation_ids": list(result.selected_ids),
            "rendered_ids": list(result.rendered_ids),
            "recorded_ids": recorded_ids,
            "feedback_ids": feedback_ids,
            "suppressed": suppressions,
            "budget_suppressed_ids": list(
                result.diagnostics.get("suppressed_reason_codes", {}).keys()
            ),
            "no_match": result.no_match,
            "no_match_reason": result.no_match_reason,
            "controller_mode": result.controller_decision.get("mode", "none"),
            "prompt_tokens": estimate_tokens(result.prompt_text),
            "io": {
                "task_start_scope_saves": task_start_saves,
                "feedback_scope_saves": feedback_saves,
                "total_scope_saves": len(manager.save_events),
                "retrieval_counter_calls": len(manager.retrieval_calls),
                "injection_counter_calls": len(manager.injection_calls),
                "feedback_calls": len(manager.feedback_calls),
            },
            "latency_ms": round((time.perf_counter() - started) * 1000, 6),
        }


def _holdout_metrics(
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    labels = {case["case_id"]: case for case in cases}
    totals: Counter[str] = Counter()
    reason_expected: set[tuple[str, str, str, str]] = set()
    reason_actual: set[tuple[str, str, str, str]] = set()
    violating_cases: list[str] = []
    incorrect_cases: list[str] = []
    exact_rendered_cases: list[str] = []
    for result in results:
        case = labels[result["case_id"]]
        relevant = set(case["primary_ids"]) | set(case["allowed_secondary_ids"])
        primary = set(case["primary_ids"])
        excluded = set(case["must_exclude_ids"])
        candidate = set(result["candidate_ids"])
        post_gate = set(result["post_gate_ids"])
        consolidated = set(result["post_consolidation_ids"])
        rendered = set(result["rendered_ids"])
        suppressed = {item["entry_id"] for item in result["suppressed"]}
        totals["relevant"] += len(relevant)
        totals["candidate_relevant"] += len(candidate & relevant)
        totals["post_gate_relevant"] += len(post_gate & relevant)
        totals["consolidated"] += len(consolidated)
        totals["consolidated_relevant"] += len(consolidated & relevant)
        totals["rendered"] += len(rendered)
        totals["rendered_relevant"] += len(rendered & relevant)
        totals["primary_case"] += bool(primary)
        totals["primary_hit"] += bool(primary & rendered)
        totals["must_exclude"] += len(excluded)
        totals["must_exclude_rendered"] += len(excluded & rendered)
        totals["incorrect_suppression"] += len(relevant & suppressed)
        if relevant & suppressed:
            incorrect_cases.append(case["case_id"])
        if excluded & rendered:
            violating_cases.append(case["case_id"])
        normalized_rendered = [
            " ".join(
                next(
                    memory["content"]
                    for memory in case["memories"]
                    if memory["id"] == entry_id
                ).lower().split()
            )
            for entry_id in result["rendered_ids"]
        ]
        totals["duplicate_render"] += len(normalized_rendered) - len(set(normalized_rendered))
        if case["category"] == "complementary_secondary":
            allowed = set(case["allowed_secondary_ids"])
            totals["complementary"] += len(allowed)
            totals["complementary_rendered"] += len(allowed & rendered)
        if case["unresolved_conflict_render_nothing"]:
            totals["unresolved_case"] += 1
            totals["unresolved_unsafe_render"] += bool(rendered)
        totals["candidate_selected_errors"] += bool(consolidated - candidate)
        totals["selected_rendered_errors"] += bool(rendered - consolidated)
        totals["rendered_recorded_errors"] += result["rendered_ids"] != result["recorded_ids"]
        totals["rendered_feedback_errors"] += result["rendered_ids"] != result["feedback_ids"]
        if set(case["expected_rendered_ids"]) == rendered:
            exact_rendered_cases.append(case["case_id"])
        for item in case["expected_suppressions"]:
            reason_expected.add(
                (
                    case["case_id"],
                    item["entry_id"],
                    item["reason"],
                    item["dominating_id"],
                )
            )
        for item in result["suppressed"]:
            reason_actual.add(
                (
                    case["case_id"],
                    item["entry_id"],
                    item["reason"],
                    item["dominating_id"],
                )
            )
    reason_union = reason_expected | reason_actual
    relevant_count = totals["relevant"]
    primary_case_count = totals["primary_case"]
    return {
        "retrieval_candidate_recall": (
            totals["candidate_relevant"] / relevant_count if relevant_count else 1.0
        ),
        "post_gate_recall": (
            totals["post_gate_relevant"] / relevant_count if relevant_count else 1.0
        ),
        "post_consolidation_precision": (
            totals["consolidated_relevant"] / totals["consolidated"]
            if totals["consolidated"]
            else 1.0
        ),
        "post_consolidation_recall": (
            totals["consolidated_relevant"] / relevant_count if relevant_count else 1.0
        ),
        "rendered_precision": (
            totals["rendered_relevant"] / totals["rendered"] if totals["rendered"] else 1.0
        ),
        "rendered_recall": (
            totals["rendered_relevant"] / relevant_count if relevant_count else 1.0
        ),
        "primary_hit_rate": (
            totals["primary_hit"] / primary_case_count if primary_case_count else 1.0
        ),
        "must_exclude_violation_rate": (
            totals["must_exclude_rendered"] / totals["must_exclude"]
            if totals["must_exclude"]
            else 0.0
        ),
        "duplicate_render_rate": (
            totals["duplicate_render"] / totals["rendered"] if totals["rendered"] else 0.0
        ),
        "unresolved_conflict_unsafe_render_rate": (
            totals["unresolved_unsafe_render"] / totals["unresolved_case"]
            if totals["unresolved_case"]
            else 0.0
        ),
        "incorrect_suppression_rate": (
            totals["incorrect_suppression"] / relevant_count if relevant_count else 0.0
        ),
        "complementary_secondary_retention_rate": (
            totals["complementary_rendered"] / totals["complementary"]
            if totals["complementary"]
            else 1.0
        ),
        "reason_code_accuracy": (
            len(reason_expected & reason_actual) / len(reason_union) if reason_union else 1.0
        ),
        "candidate_selected_disagreement_count": totals["candidate_selected_errors"],
        "selected_rendered_disagreement_count": totals["selected_rendered_errors"],
        "rendered_recorded_disagreement_count": totals["rendered_recorded_errors"],
        "rendered_feedback_disagreement_count": totals["rendered_feedback_errors"],
        "exact_rendered_id_set_rate": len(exact_rendered_cases) / len(cases),
        "must_exclude_violation_cases": violating_cases,
        "incorrect_suppression_cases": incorrect_cases,
        "counts": dict(sorted(totals.items())),
    }


def _metrics_by_category(
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    categories = sorted({case["category"] for case in cases})
    return {
        category: _holdout_metrics(
            [case for case in cases if case["category"] == category],
            [result for result in results if result["category"] == category],
        )
        for category in categories
    }


def deterministic_phase2b_view(report: dict[str, Any]) -> dict[str, Any]:
    view = copy.deepcopy(report)
    view.pop("performance", None)
    view.get("holdout", {}).pop("peak_memory_bytes", None)
    for result in view.get("holdout", {}).get("cases", []):
        result.pop("latency_ms", None)
    view.get("phase2a_80_case", {}).pop("latency", None)
    return view


def _benchmark_candidates(count: int) -> tuple[Any, ...]:
    from minicode.memory_retrieval import RetrievedMemory, RetrievalScore

    return tuple(
        RetrievedMemory(
            entry_id=f"stress-{index:04d}",
            scope="project",
            category="architecture",
            content=f"Component object{index // 5} setting value{index} remains independent.",
            score=RetrievalScore(
                lexical_score=0.8,
                final_score=0.8 - index / max(1, count * 1000),
                matched_terms=(f"object-{index // 5}", "operation"),
            ),
            rank=index + 1,
            token_count=12,
            truncated=False,
            source="project_memory",
            tags=(f"object-{index // 5}",),
            domains=("backend",),
        )
        for index in range(count)
    )


def benchmark_consolidator() -> dict[str, Any]:
    from minicode.memory_candidate_consolidation import CandidateConsolidator
    from minicode.memory_retrieval import MemoryRetrievalRequest

    consolidator = CandidateConsolidator(max_candidates=256)
    request = MemoryRetrievalRequest(query="Review component operation settings")
    report: dict[str, Any] = {}
    for count, repetitions in ((100, 40), (500, 15), (1000, 10)):
        candidates = _benchmark_candidates(count)
        consolidator.consolidate(candidates, request)
        samples: list[float] = []
        for _ in range(repetitions):
            started = time.perf_counter()
            result = consolidator.consolidate(candidates, request)
            samples.append((time.perf_counter() - started) * 1000)
        tracemalloc.start()
        consolidator.consolidate(candidates, request)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        report[str(count)] = {
            "repetitions": repetitions,
            "p50_ms": statistics.median(samples),
            "p95_ms": _percentile(samples, 0.95),
            "max_ms": max(samples),
            "peak_memory_bytes": peak,
            "retained_count": len(result.retained),
            "suppressed_count": len(result.suppressed),
        }
    return report


class _NetworkGuard:
    def __init__(self) -> None:
        self.call_count = 0

    def blocked(self, *_args: Any, **_kwargs: Any) -> None:
        self.call_count += 1
        raise AssertionError("network access is forbidden in the Phase 2B evaluator")


def evaluate_phase2b(
    *,
    project_root: Path,
    holdout_path: Path,
    phase1_dataset_root: Path,
    phase2a_baseline_path: Path,
    formal_root: Path | None = None,
    enforcement_mode: str = "advisory",
) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    formal_root = Path(formal_root or (Path.home() / ".mini-code")).resolve()
    holdout_path = Path(holdout_path).resolve()
    phase1_dataset_root = Path(phase1_dataset_root).resolve()
    phase2a_baseline_path = Path(phase2a_baseline_path).resolve()
    formal_before = snapshot_tree(formal_root)
    holdout_hash_before = _sha256(holdout_path)
    phase1_frozen_before = _hash_paths(project_root, PHASE1_FROZEN_HASHES)
    phase2a_frozen_before = _hash_paths(project_root, PHASE2A_FROZEN_HASHES)
    cases = load_holdout(holdout_path)
    network_guard = _NetworkGuard()
    with patch.object(socket, "create_connection", network_guard.blocked), patch.object(
        socket.socket, "connect", network_guard.blocked
    ):
        first_results = [_evaluate_holdout_case(case) for case in cases]
        tracemalloc.start()
        second_results = [_evaluate_holdout_case(case) for case in cases]
        _, holdout_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        with tempfile.TemporaryDirectory(prefix="minicode-phase2b-eval-home-") as temporary:
            phase2a = evaluate_phase2a_dataset(
                phase1_dataset_root,
                project_root=project_root,
                baseline_path=phase2a_baseline_path,
                home=Path(temporary),
            )
        stress = benchmark_consolidator()
    holdout_metrics = _holdout_metrics(cases, first_results)
    category_metrics = _metrics_by_category(cases, first_results)
    deterministic = (
        [
            {key: value for key, value in result.items() if key != "latency_ms"}
            for result in first_results
        ]
        == [
            {key: value for key, value in result.items() if key != "latency_ms"}
            for result in second_results
        ]
    )
    canonical = phase2a["overall_metrics"]["canonical_retrieval"]
    phase1_cases = {case["case_id"]: case for case in load_dataset(phase1_dataset_root)}
    remaining_violations: list[dict[str, Any]] = []
    unresolved_unsafe = 0
    for case_result in phase2a["per_case_results"]:
        result = case_result["arms"]["canonical_retrieval"]
        labels = phase1_cases[case_result["case_id"]]
        violations = sorted(set(result["rendered_ids"] or []) & set(labels["must_exclude_ids"]))
        if violations:
            remaining_violations.append(
                {"case_id": case_result["case_id"], "entry_ids": violations}
            )
        unresolved_ids = {
            item["entry_id"]
            for item in result["diagnostics"].get("consolidation_suppressions", [])
            if item["reason"] == "unresolved_conflict"
        }
        unresolved_unsafe += len(unresolved_ids & set(result["rendered_ids"] or []))
    phase2a_quality = {
        "precision_at_1": canonical["precision_at_1"],
        "recall_at_5": canonical["recall_at_5"],
        "primary_hit_rate": canonical["primary_hit_rate"],
        "rendered_precision": canonical["actual_rendered_precision"],
        "must_exclude_violation_rate": canonical["must_exclude_violation_rate"],
        "negative_false_injection_rate": canonical["negative_false_injection_rate"],
        "duplicate_rendered_rate": canonical["duplicate_injection_rate"],
        "unresolved_conflict_unsafe_render_count": unresolved_unsafe,
        "returned_rendered_disagreement_rate": canonical[
            "returned_rendered_disagreement_rate"
        ],
        "rendered_recorded_disagreement_rate": canonical[
            "rendered_recorded_disagreement_rate"
        ],
        "rendered_feedback_disagreement_rate": canonical[
            "rendered_feedback_disagreement_rate"
        ],
        "hard_memory_budget_violation_rate": canonical["max_memories_violation_rate"],
        "hard_token_budget_violation_rate": canonical["token_budget_violation_rate"],
    }
    phase2a_gates = {
        "rendered_precision_at_least_0_95": phase2a_quality["rendered_precision"] >= 0.95,
        "must_exclude_at_most_0_05": phase2a_quality["must_exclude_violation_rate"] <= 0.05,
        "negative_false_injection_zero": phase2a_quality["negative_false_injection_rate"] == 0,
        "recall_at_5_at_least_0_95": phase2a_quality["recall_at_5"] >= 0.95,
        "primary_hit_at_least_0_985": phase2a_quality["primary_hit_rate"] >= 0.985,
        "precision_at_1_not_below_0_8625": phase2a_quality["precision_at_1"] >= 0.8625,
        "duplicate_render_zero": phase2a_quality["duplicate_rendered_rate"] == 0,
        "unresolved_unsafe_render_zero": unresolved_unsafe == 0,
        "id_disagreement_zero": all(
            phase2a_quality[name] == 0
            for name in (
                "returned_rendered_disagreement_rate",
                "rendered_recorded_disagreement_rate",
                "rendered_feedback_disagreement_rate",
            )
        ),
        "hard_budget_violation_zero": (
            phase2a_quality["hard_memory_budget_violation_rate"] == 0
            and phase2a_quality["hard_token_budget_violation_rate"] == 0
        ),
        "phase2a_correctness_gates_preserved": all(phase2a["correctness_gates"].values()),
    }
    holdout_gates = {
        "post_consolidation_precision_at_least_0_95": (
            holdout_metrics["post_consolidation_precision"] >= 0.95
        ),
        "incorrect_suppression_at_most_0_02": (
            holdout_metrics["incorrect_suppression_rate"] <= 0.02
        ),
        "complementary_retention_at_least_0_95": (
            holdout_metrics["complementary_secondary_retention_rate"] >= 0.95
        ),
        "must_exclude_at_most_0_05": (
            holdout_metrics["must_exclude_violation_rate"] <= 0.05
        ),
        "unresolved_unsafe_render_zero": (
            holdout_metrics["unresolved_conflict_unsafe_render_rate"] == 0
        ),
        "reason_accuracy_at_least_0_95": holdout_metrics["reason_code_accuracy"] >= 0.95,
        "deterministic": deterministic,
    }
    full_p95 = phase2a["latency"]["canonical_retrieval"]["p95_ms"]
    performance_policy = evaluate_performance_policy(
        canonical_p95_ms=full_p95,
        consolidator_100_p95_ms=stress["100"]["p95_ms"],
        retained_count_500=stress["500"]["retained_count"],
        retained_count_1000=stress["1000"]["retained_count"],
        network_call_count=network_guard.call_count,
        enforcement_mode=enforcement_mode,
    )
    task_start_saves = [result["io"]["task_start_scope_saves"] for result in first_results]
    total_saves = [result["io"]["total_scope_saves"] for result in first_results]
    formal_after = snapshot_tree(formal_root)
    phase1_frozen_after = _hash_paths(project_root, PHASE1_FROZEN_HASHES)
    phase2a_frozen_after = _hash_paths(project_root, PHASE2A_FROZEN_HASHES)
    integrity_gates = {
        "formal_tree_unchanged": formal_before == formal_after,
        "holdout_unchanged": holdout_hash_before == _sha256(holdout_path),
        "phase1_frozen_matches": (
            phase1_frozen_before["matches"] and phase1_frozen_after["matches"]
        ),
        "phase2a_frozen_matches": (
            phase2a_frozen_before["matches"] and phase2a_frozen_after["matches"]
        ),
    }
    acceptance = (
        all(phase2a_gates.values())
        and all(holdout_gates.values())
        and all(performance_policy["deterministicGates"].values())
        and all(integrity_gates.values())
    )
    return {
        "schema_version": "memory-retrieval-phase2b-v1",
        "evaluator_version": PHASE2B_EVALUATOR_VERSION,
        "synthetic_data": True,
        "acceptance_passed": acceptance,
        "phase2a_80_case": {
            "case_count": phase2a["dataset_case_count"],
            "metrics": phase2a_quality,
            "gates": phase2a_gates,
            "per_category_metrics": {
                category: values["canonical_retrieval"]
                for category, values in phase2a["per_category_metrics"].items()
            },
            "remaining_must_exclude_violations": remaining_violations,
            "identity_counts": phase2a["identity_counts"]["canonical_retrieval"],
            "no_match": phase2a["no_match"]["canonical_retrieval"],
            "latency": phase2a["latency"]["canonical_retrieval"],
            "save_io": phase2a["save_io"]["canonical_retrieval"],
        },
        "holdout": {
            "case_count": len(cases),
            "category_counts": dict(sorted(Counter(case["category"] for case in cases).items())),
            "metrics": holdout_metrics,
            "gates": holdout_gates,
            "per_category_metrics": category_metrics,
            "cases": first_results,
            "peak_memory_bytes": holdout_peak,
        },
        "performance": {
            "consolidator": stress,
            "phase2a_reference_canonical_p95_ms": PHASE2A_CANONICAL_P95_MS,
            "material_limit_ms": PHASE2A_P95_MATERIAL_LIMIT_MS,
            "current_canonical_p95_ms": full_p95,
            **performance_policy,
            "observations": {
                "canonicalLatencyMs": copy.deepcopy(
                    phase2a["latency"]["canonical_retrieval"]
                ),
                "consolidator": copy.deepcopy(stress),
                "holdoutPeakMemoryBytes": holdout_peak,
            },
            "complexity": "O(N log N + P + B^2), with deterministic buckets and B<=256",
        },
        "io": {
            "average_task_start_scope_saves": statistics.mean(task_start_saves),
            "max_task_start_scope_saves": max(task_start_saves),
            "average_full_turn_scope_saves": statistics.mean(total_saves),
            "max_full_turn_scope_saves": max(total_saves),
            "suppressed_candidates_add_saves": False,
        },
        "remote_call_count": network_guard.call_count,
        "determinism": {"two_holdout_runs_equal_without_latency": deterministic},
        "integrity": {
            "gates": integrity_gates,
            "formal_tree_before": _tree_summary(formal_before),
            "formal_tree_after": _tree_summary(formal_after),
            "holdout_sha256": holdout_hash_before,
            "phase1_frozen_before": phase1_frozen_before,
            "phase1_frozen_after": phase1_frozen_after,
            "phase2a_frozen_before": phase2a_frozen_before,
            "phase2a_frozen_after": phase2a_frozen_after,
        },
        "limitations": [
            "The 80-case and 33-case datasets are synthetic regressions, not a production distribution.",
            "Conflict and duplicate decisions require deterministic lexical or structured evidence; semantic-only equivalence remains out of scope.",
            "No embedding, vector database, LLM, reranker provider, query rewrite, or remote service is used.",
            "The consolidator cap is 256 post-gate candidates; overflow fails closed with candidate_limit diagnostics.",
            "Latency and peak memory are environment-sensitive and excluded from deterministic equality.",
            "Wall-clock observations are advisory unless strict enforcement is selected explicitly.",
        ],
    }


def _format_metric(value: Any) -> str:
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def render_accuracy_markdown(report: dict[str, Any]) -> str:
    old = json.loads(
        (Path(__file__).resolve().parents[1] / "artifacts" / "memory-retrieval-phase2a.json").read_text(
            encoding="utf-8"
        )
    )["overall_metrics"]["canonical_retrieval"]
    current = report["phase2a_80_case"]["metrics"]
    holdout = report["holdout"]["metrics"]
    lines = [
        "# Memory Retrieval Phase 2B",
        "",
        "> Deterministic post-gate consolidation on the frozen 80-case suite and a 33-case independent holdout.",
        "",
        "## Acceptance",
        "",
        f"- Deterministic acceptance: `{report['acceptance_passed']}`",
        f"- Frozen 80-case gates: `{all(report['phase2a_80_case']['gates'].values())}`",
        f"- Holdout gates: `{all(report['holdout']['gates'].values())}`",
        f"- Deterministic performance invariants: `{report['performance']['deterministicPassed']}`",
        f"- Integrity gates: `{all(report['integrity']['gates'].values())}`",
        f"- Wall-clock enforcement mode: `{report['performance']['enforcementMode']}`",
        f"- Strict wall-clock result: `{report['performance']['strictPassed']}`",
        f"- Remote calls: `{report['remote_call_count']}`",
        "",
        "## Frozen 80 Cases",
        "",
        "| Metric | Phase 2A | Phase 2B |",
        "|---|---:|---:|",
        f"| Precision@1 | {_format_metric(old['precision_at_1'])} | {_format_metric(current['precision_at_1'])} |",
        f"| Recall@5 | {_format_metric(old['recall_at_5'])} | {_format_metric(current['recall_at_5'])} |",
        f"| Primary hit | {_format_metric(old['primary_hit_rate'])} | {_format_metric(current['primary_hit_rate'])} |",
        f"| Rendered precision | {_format_metric(old['actual_rendered_precision'])} | {_format_metric(current['rendered_precision'])} |",
        f"| Must-exclude rate | {_format_metric(old['must_exclude_violation_rate'])} | {_format_metric(current['must_exclude_violation_rate'])} |",
        "",
        "## Phase 2B Holdout",
        "",
    ]
    for name in (
        "retrieval_candidate_recall",
        "post_gate_recall",
        "post_consolidation_precision",
        "post_consolidation_recall",
        "rendered_precision",
        "rendered_recall",
        "incorrect_suppression_rate",
        "complementary_secondary_retention_rate",
        "reason_code_accuracy",
    ):
        lines.append(f"- `{name}`: `{_format_metric(holdout[name])}`")
    lines.extend(["", "## Limits", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def render_comparison_markdown(report: dict[str, Any]) -> str:
    old = json.loads(
        (Path(__file__).resolve().parents[1] / "artifacts" / "memory-retrieval-phase2a.json").read_text(
            encoding="utf-8"
        )
    )["overall_metrics"]["canonical_retrieval"]
    current = report["phase2a_80_case"]["metrics"]
    rows = (
        ("Precision@1", old["precision_at_1"], current["precision_at_1"]),
        ("Recall@5", old["recall_at_5"], current["recall_at_5"]),
        ("Primary hit", old["primary_hit_rate"], current["primary_hit_rate"]),
        ("Rendered precision", old["actual_rendered_precision"], current["rendered_precision"]),
        ("Must-exclude violation", old["must_exclude_violation_rate"], current["must_exclude_violation_rate"]),
    )
    lines = [
        "# Memory Retrieval Phase 2A vs Phase 2B",
        "",
        "| Metric | Phase 2A | Phase 2B | Delta |",
        "|---|---:|---:|---:|",
    ]
    for label, before, after in rows:
        lines.append(
            f"| {label} | {_format_metric(before)} | {_format_metric(after)} | {_format_metric(after - before)} |"
        )
    lines.extend(
        [
            "",
            "Phase 2B changes only the post-gate candidate set. Retrieval candidate generation, relevance gating, controller policy, hard budgets, counters, and rendered-only feedback remain the existing Phase 2A boundaries.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_performance_markdown(report: dict[str, Any]) -> str:
    performance = report["performance"]
    lines = [
        "# Memory Retrieval Phase 2B Performance",
        "",
        "| Input candidates | Retained | Suppressed | P50 ms | P95 ms | Peak bytes |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for count in ("100", "500", "1000"):
        row = performance["consolidator"][count]
        lines.append(
            f"| {count} | {row['retained_count']} | {row['suppressed_count']} | "
            f"{row['p50_ms']:.4f} | {row['p95_ms']:.4f} | {row['peak_memory_bytes']} |"
        )
    lines.extend(
        [
            "",
            f"- Enforcement mode: `{performance['enforcementMode']}`.",
            f"- Strict wall-clock result: `{performance['strictPassed']}`.",
            f"- Deterministic performance invariants: `{performance['deterministicPassed']}`.",
            f"- Full canonical P95: `{performance['current_canonical_p95_ms']:.4f} ms`.",
            f"- Phase 2A reference: `{performance['phase2a_reference_canonical_p95_ms']:.4f} ms`.",
            f"- Material limit: `{performance['material_limit_ms']:.4f} ms`.",
            f"- Wall-clock gates: `{json.dumps(performance['wallClockGates'], sort_keys=True)}`.",
            f"- Complexity bound: `{performance['complexity']}`.",
            f"- Average task-start saves: `{report['io']['average_task_start_scope_saves']:.4f}`.",
            f"- Average full-turn saves: `{report['io']['average_full_turn_scope_saves']:.4f}`.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_phase2b_reports(
    report: dict[str, Any],
    *,
    json_path: Path,
    markdown_path: Path,
    comparison_path: Path,
    performance_path: Path,
) -> None:
    for path in (json_path, markdown_path, comparison_path, performance_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_accuracy_markdown(report), encoding="utf-8")
    comparison_path.write_text(render_comparison_markdown(report), encoding="utf-8")
    performance_path.write_text(render_performance_markdown(report), encoding="utf-8")
