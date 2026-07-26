"""Frozen offline evaluator used with versioned Retrieval source certification."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import socket
import stat
import statistics
import tempfile
import time
import tracemalloc
import unicodedata
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import jsonschema

from scripts.memory_retrieval_phase2b_evaluator import (
    PHASE1_FROZEN_HASHES,
    PHASE2A_FROZEN_HASHES,
)
from scripts.memory_retrieval_production_baseline import (
    ACTIVE_PRODUCTION_RETRIEVAL_HASHES,
    verify_active_baseline,
)


EVALUATOR_VERSION = "1.1.0"
REFERENCE_TIME = 1736942400.0
DATASET_MANIFEST_SHA256 = "59638f40dc76df881c63804275eda5cf137679b77b72916694635b5c51ac9f8b"
ACCEPTED_V1_ARTIFACT_SHA256 = (
    "5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b"
)
ACCEPTED_V1_BEHAVIOR_PROJECTION_SHA256 = (
    "b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60"
)
MAX_CASE_MEMORIES = 8
MAX_BACKGROUND_MEMORIES = 32
MAX_CONTENT_CHARS = 4000
MAX_METADATA_BYTES = 4096
MAX_METADATA_DEPTH = 4
MAX_METADATA_NODES = 64
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "do",
    "does", "for", "from", "had", "has", "have", "i", "if", "in", "into",
    "is", "it", "its", "not", "of", "on", "or", "our", "that", "the", "their",
    "then", "this", "to", "was", "we", "when", "with", "you", "your",
}

PRODUCTION_RETRIEVAL_HASHES = ACTIVE_PRODUCTION_RETRIEVAL_HASHES

PHASE2B_FROZEN_HASHES = {
    "artifacts/memory-retrieval-phase2b.json": "2d082e1aa50c1461a78ef5e18c56b59533460a140634effb911fd6c5b4bd3996",
    "artifacts/memory-retrieval-phase2b.schema.json": "a0a9a8093e9970d1fcd275f9d7670804b8b2ecd67ec468b45c13b5ee3390820a",
    "docs/memory-retrieval-phase2b-comparison.md": "6e2649e0345f6ec58433d3863a160e8cceb8e8828253cfec842faf35951113e5",
    "docs/memory-retrieval-phase2b-performance.md": "3cff028426be913baa06cacbd2eff69b3141f74ff16528d5e44b4f37416a5235",
    "docs/memory-retrieval-phase2b.md": "9ec83beff0ab5a5c0b2af3fd65e62f37b441a4416e556b98c751032e51027da9",
    "scripts/evaluate_memory_retrieval_phase2b.py": "841883544b031ff5b58ea759a2688413637e70143cd231708514843700ed05dd",
    "scripts/memory_retrieval_phase2b_evaluator.py": "e8c075c3e114c2c5f9c1645e1b53ea365973de883eb3f6a8b2c833ecbef0765d",
    "tests/fixtures/memory_retrieval_phase2b_holdout.json": "5ceb46134d0d17060c7b635bb99aeae8a43c799a3f6dd40a07d65978930b1136",
    "tests/fixtures/memory_retrieval_phase2b_holdout.schema.json": "c1d4461fcf2e23949585d0742fd20af4d2486d05f1406ad3469c204a21a83ae4",
    "tests/test_memory_candidate_consolidation.py": "4c7011ba7168388b88fc58a3fe253366a3d5c19dd68dac36c50c8febdf4de67c",
    "tests/test_memory_retrieval_phase2b.py": "496882681aaa5d3281b66669d4d4b8a31a785386400d02a1009e6cee59b8548b",
    "tests/test_memory_retrieval_phase2b_evaluator.py": "828bf028c91ed00c6d3d103d4d84e8c5632a0fddd28022b0c6cc11af3f8537c3",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_paths(root: Path, expected: dict[str, str]) -> dict[str, Any]:
    actual = {
        relative: sha256_file(root / relative) if (root / relative).is_file() else "missing"
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
        "manifest_sha256": hashlib.sha256(
            json.dumps(actual, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def snapshot_tree(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            stat = path.stat()
            files.append(
                {
                    "path": str(path.relative_to(root)),
                    "sha256": sha256_file(path),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
    return {"root": str(root), "file_count": len(files), "files": files}


def snapshot_isolated_case_tree(root: Path) -> dict[str, Any]:
    """Snapshot semantic case outputs, excluding the validated coordination lock."""
    lock_relative = Path("home/.mini-code/memory-store.lock")
    lock_path = root / lock_relative
    if lock_path.exists() or lock_path.is_symlink():
        lock_stat = lock_path.lstat()
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or stat.S_IMODE(lock_stat.st_mode) != 0o600
            or lock_stat.st_size != 0
        ):
            raise AssertionError("invalid memory-store coordination lock artifact")
    snapshot = snapshot_tree(root)
    files = [
        item for item in snapshot["files"] if item["path"] != lock_relative.as_posix()
    ]
    return {"root": snapshot["root"], "file_count": len(files), "files": files}


def tree_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    files = snapshot["files"]
    encoded = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "file_count": snapshot["file_count"],
        "total_size": sum(item["size"] for item in files),
        "manifest_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "mtime_min_ns": min((item["mtime_ns"] for item in files), default=None),
        "mtime_max_ns": max((item["mtime_ns"] for item in files), default=None),
    }


def verify_frozen_dataset(dataset_root: Path) -> dict[str, Any]:
    manifest_path = dataset_root / "frozen.sha256"
    manifest_sha = sha256_file(manifest_path)
    expected: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        digest, filename = line.split("  ", 1)
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or Path(filename).name != filename:
            raise ValueError("invalid dataset freeze manifest line")
        expected[filename] = digest
    actual = {
        filename: sha256_file(dataset_root / filename)
        if (dataset_root / filename).is_file()
        else "missing"
        for filename in expected
    }
    mismatches = {
        filename: {"expected": expected[filename], "actual": actual[filename]}
        for filename in expected
        if expected[filename] != actual[filename]
    }
    return {
        "file_count": len(expected),
        "manifest_sha256": manifest_sha,
        "expected_manifest_sha256": DATASET_MANIFEST_SHA256,
        "matches": not mismatches and manifest_sha == DATASET_MANIFEST_SHA256,
        "mismatches": mismatches,
    }


def diagnostic_tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    ascii_terms = {
        term for term in re.findall(r"[a-z0-9]+", normalized) if term not in STOP_WORDS
    }
    chinese: set[str] = set()
    for run in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", normalized):
        if len(run) >= 2:
            chinese.update(run[index : index + 2] for index in range(len(run) - 1))
    return ascii_terms | chinese


def normalized_token_overlap(query: str, content: str) -> tuple[float, str]:
    query_tokens = diagnostic_tokens(query)
    content_tokens = diagnostic_tokens(content)
    union = query_tokens | content_tokens
    value = round(len(query_tokens & content_tokens) / len(union), 6) if union else 0.0
    bucket = (
        "zero"
        if value == 0
        else "low"
        if value <= 0.08
        else "medium"
        if value <= 0.25
        else "high"
    )
    return value, bucket


def validate_bounded_value(value: Any) -> None:
    seen: set[int] = set()
    nodes = 0

    def visit(current: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_METADATA_NODES:
            raise ValueError("metadata node limit exceeded")
        if depth > MAX_METADATA_DEPTH:
            raise ValueError("metadata depth limit exceeded")
        if isinstance(current, (dict, list)):
            identity = id(current)
            if identity in seen:
                raise ValueError("cyclic metadata is not allowed")
            seen.add(identity)
            nested = current.values() if isinstance(current, dict) else current
            for item in nested:
                visit(item, depth + 1)
            seen.remove(identity)
        elif not isinstance(current, (str, int, float, bool, type(None))):
            raise ValueError("metadata contains an unserializable value")

    visit(value, 0)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValueError("metadata byte limit exceeded")


def _validate_cases(cases: list[dict[str, Any]], background: list[dict[str, Any]]) -> None:
    case_ids = [case["case_id"] for case in cases]
    entry_ids = [entry["id"] for case in cases for entry in case["memories"]]
    entry_ids.extend(entry["id"] for entry in background)
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case_id values must be unique")
    if len(entry_ids) != len(set(entry_ids)):
        raise ValueError("entry_id values must be globally unique")
    known_cases = set(case_ids)
    for case in cases:
        if len(case["memories"]) > MAX_CASE_MEMORIES:
            raise ValueError(f"{case['case_id']}: case memory limit exceeded")
        primary = set(case["primary_entry_ids"])
        secondary = set(case["allowed_secondary_ids"])
        excluded = set(case["must_exclude_ids"])
        local_ids = {entry["id"] for entry in case["memories"]}
        if primary & secondary or primary & excluded or secondary & excluded:
            raise ValueError(f"{case['case_id']}: labels are not mutually exclusive")
        if not primary | secondary | excluded <= local_ids:
            raise ValueError(f"{case['case_id']}: labels reference unknown entries")
        if not set(case["hard_negative_control_case_ids"]) <= known_cases:
            raise ValueError(f"{case['case_id']}: unknown hard-negative control")
        comparison_ids = primary or excluded
        comparison = " ".join(
            entry["content"] for entry in case["memories"] if entry["id"] in comparison_ids
        )
        expected_overlap = normalized_token_overlap(case["query"], comparison)
        stored_overlap = (
            case["normalized_token_overlap"],
            case["lexical_overlap_class"],
        )
        if expected_overlap != stored_overlap:
            raise ValueError(f"{case['case_id']}: token overlap label is stale")
        for entry in case["memories"]:
            if len(entry["content"]) > MAX_CONTENT_CHARS:
                raise ValueError(f"{entry['id']}: content limit exceeded")
            validate_bounded_value(entry["metadata"])
            validate_bounded_value(entry["provenance"])
        if case["polarity"] == "positive":
            if len(primary) != 1 or excluded or case["expected_no_match"]:
                raise ValueError(f"{case['case_id']}: invalid positive labels")
            target = next(entry for entry in case["memories"] if entry["id"] in primary)
            if not (
                target["lifecycle_status"] == "active"
                and target["safety_status"] == "safe"
                and target["approval_status"] == "approved"
                and not target["curator_locked"]
                and target["tier"] != "archival"
            ):
                raise ValueError(f"{case['case_id']}: positive primary is not injectable")
        elif primary or not excluded or not case["expected_no_match"]:
            raise ValueError(f"{case['case_id']}: invalid hard-negative labels")


def load_dataset(dataset_root: Path) -> dict[str, Any]:
    dataset_root = Path(dataset_root).resolve()
    freeze = verify_frozen_dataset(dataset_root)
    if not freeze["matches"]:
        raise ValueError(f"dataset freeze mismatch: {freeze['mismatches']}")
    schema = json.loads((dataset_root / "schema.json").read_text(encoding="utf-8"))
    manifest = json.loads((dataset_root / "manifest.json").read_text(encoding="utf-8"))
    jsonschema.validate(manifest, schema)
    background_document = json.loads(
        (dataset_root / manifest["background_file"]).read_text(encoding="utf-8")
    )
    jsonschema.validate(background_document, schema)
    cases: list[dict[str, Any]] = []
    for filename in manifest["case_files"]:
        document = json.loads((dataset_root / filename).read_text(encoding="utf-8"))
        jsonschema.validate(document, schema)
        cases.extend(document["cases"])
    if len(background_document["memories"]) != MAX_BACKGROUND_MEMORIES:
        raise ValueError("background pool must contain exactly 32 entries")
    _validate_cases(cases, background_document["memories"])
    counts = Counter(case["polarity"] for case in cases)
    splits = Counter(case["split"] for case in cases)
    if counts != {"positive": 72, "hard_negative": 36}:
        raise ValueError("dataset polarity counts do not match frozen contract")
    if splits != {"analysis": 72, "sealed": 36}:
        raise ValueError("dataset split counts do not match frozen contract")
    return {
        "root": dataset_root,
        "manifest": manifest,
        "freeze": freeze,
        "background": copy.deepcopy(background_document["memories"]),
        "cases": sorted(copy.deepcopy(cases), key=lambda item: item["case_id"]),
    }


class _InstrumentedManagerMixin:
    def _initialize_observation(self) -> None:
        self.save_events: list[str] = []
        self.retrieval_calls: list[list[str]] = []
        self.injection_calls: list[list[str]] = []
        self.feedback_calls: list[dict[str, Any]] = []

    def _save_scope(self, scope: Any) -> None:
        self.save_events.append(scope.value)
        super()._save_scope(scope)

    def record_retrievals(self, entry_ids: list[str]) -> None:
        self.retrieval_calls.append(list(entry_ids))
        super().record_retrievals(entry_ids)

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


def _entry_counter_snapshot(manager: Any) -> dict[str, tuple[int, int, int, int]]:
    return {
        entry.id: (
            entry.retrieval_count,
            entry.injection_count,
            entry.success_count,
            entry.failure_count,
        )
        for memory_file in manager.memories.values()
        for entry in memory_file.entries
    }


def _memory_entry(spec: dict[str, Any]) -> Any:
    from minicode.memory import MemoryEntry

    data = copy.deepcopy(spec)
    data["last_accessed"] = REFERENCE_TIME - 3600
    data["last_used"] = 0.0
    return MemoryEntry.from_dict(data)


@contextmanager
def isolated_case_manager(
    case: dict[str, Any],
    background: list[dict[str, Any]],
) -> Iterator[tuple[Any, Path]]:
    with tempfile.TemporaryDirectory(prefix="minicode-phase3a-case-") as temporary:
        root = Path(temporary)
        home = root / "home"
        workspace = root / "workspace"
        home.mkdir()
        workspace.mkdir()
        with patch.dict(
            os.environ,
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "MINICODE_PYTEST_HOME": str(home),
            },
        ):
            from minicode import memory as memory_module
            from minicode.memory import MemoryManager

            class InstrumentedMemoryManager(_InstrumentedManagerMixin, MemoryManager):
                def __init__(self, *args: Any, **kwargs: Any) -> None:
                    self._initialize_observation()
                    super().__init__(*args, **kwargs)

            with patch.object(memory_module, "MINI_CODE_DIR", home / ".mini-code"):
                manager = InstrumentedMemoryManager(project_root=workspace)
            for spec in (*background, *case["memories"]):
                entry = _memory_entry(spec)
                manager.memories[entry.scope].entries.append(entry)
            for memory_file in manager.memories.values():
                memory_file._rebuild_indices()
            yield manager, root


def _flatten(calls: list[list[str]]) -> list[str]:
    return [entry_id for call in calls for entry_id in call]


def _rank(entry_id: str, entry_ids: list[str] | tuple[str, ...]) -> int | None:
    try:
        return list(entry_ids).index(entry_id) + 1
    except ValueError:
        return None


def _arm_view(result: Any) -> dict[str, Any]:
    scores = {
        item.entry_id: {
            "rank": item.rank,
            "final_score": round(item.score.final_score, 8),
            "lexical_score": round(item.score.lexical_score, 8),
            "matched_terms": list(item.score.matched_terms),
            "reason_codes": list(item.reason_codes),
        }
        for item in result.candidates[:50]
    }
    return {
        "candidate_ids_top20": list(result.candidate_ids[:20]),
        "candidate_ids_top50": list(result.candidate_ids[:50]),
        "candidate_count": len(result.candidate_ids),
        "post_gate_ids": list(result.diagnostics.get("post_gate_ids", [])),
        "post_consolidation_ids": list(result.selected_ids),
        "rendered_ids": list(result.rendered_ids),
        "suppressed_ids": list(result.suppressed_ids),
        "no_match": result.no_match,
        "no_match_reason": result.no_match_reason,
        "controller_mode": result.controller_decision.get("mode", "none"),
        "total_tokens": result.total_tokens,
        "scores_top50": scores,
        "consolidation_suppressions": [
            {
                "entry_id": item.get("entry_id", ""),
                "reason": item.get("reason", ""),
                "dominating_id": item.get("dominating_candidate_id", ""),
                "reason_codes": list(item.get("reason_codes", [])),
            }
            for item in result.diagnostics.get("consolidation_suppressions", [])
        ],
    }


def _manager_arm(manager: Any, case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    entries = manager.search(
        case["query"],
        scope=None,
        limit=50,
        min_relevance=0.0,
        active_domains=case["active_domains"] or None,
        record_usage=False,
    )
    return {
        "candidate_ids_top20": [entry.id for entry in entries[:20]],
        "candidate_ids_top50": [entry.id for entry in entries[:50]],
        "candidate_count": len(entries),
        "scores_top50": {
            entry.id: {
                "rank": index,
                "manager_relevance": round(float(getattr(entry, "_last_relevance", 0.0)), 8),
            }
            for index, entry in enumerate(entries[:50], 1)
        },
        "latency_ms": round((time.perf_counter() - started) * 1000, 6),
    }


def _diagnostic_arm(manager: Any, case: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    from minicode.memory_retrieval import (
        CanonicalMemoryRetriever,
        MemoryRetrievalRequest,
        RetrievalSource,
    )

    request = MemoryRetrievalRequest(
        query=case["query"],
        current_files=tuple(case["current_files"]),
        active_domains=tuple(case["active_domains"]),
        context_usage=0.0,
        max_memories=20,
        max_total_tokens=8000,
        max_tokens_per_memory=400,
        min_relevance=0.0,
        source_entrypoint=RetrievalSource.CANONICAL,
    )
    started = time.perf_counter()
    result = CanonicalMemoryRetriever(manager).retrieve(request)
    view = _arm_view(result)
    view["latency_ms"] = round((time.perf_counter() - started) * 1000, 6)
    return result, view


def _production_arm(manager: Any, case: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    from minicode.memory_pipeline import MemoryPipeline

    pipeline = MemoryPipeline(manager)
    pipeline.initialize(
        model_adapter=None,
        workspace_path=manager.workspace,
        enable_reranker=False,
        enable_vector=False,
    )
    if pipeline._injector is not None:
        pipeline._injector._injection_cooldown = 0.0
    messages = [{"role": "system", "content": "SYNTHETIC_SYSTEM_BASE"}]
    save_start = len(manager.save_events)
    started = time.perf_counter()
    pipeline.inject(
        case["query"],
        case["current_files"],
        messages,
        context_usage=case["context_usage"],
        active_domains=case["active_domains"],
    )
    result = pipeline.last_retrieval_result
    task_start_saves = len(manager.save_events) - save_start
    if result is None:
        raise AssertionError(f"{case['case_id']}: production pipeline returned no diagnostics")
    pipeline.feedback(True)
    feedback_saves = len(manager.save_events) - save_start - task_start_saves
    view = _arm_view(result)
    view.update(
        {
            "retrieval_counter_ids": _flatten(manager.retrieval_calls),
            "injection_counter_ids": _flatten(manager.injection_calls),
            "feedback_ids": [
                entry_id
                for call in manager.feedback_calls
                for entry_id in call["entry_ids"]
            ],
            "task_start_scope_saves": task_start_saves,
            "feedback_scope_saves": feedback_saves,
            "total_scope_saves": len(manager.save_events) - save_start,
            "system_prompt_changed": messages[0]["content"] != "SYNTHETIC_SYSTEM_BASE",
            "latency_ms": round((time.perf_counter() - started) * 1000, 6),
        }
    )
    return result, view


def _first_loss(case: dict[str, Any], diagnostic: dict[str, Any], production: dict[str, Any]) -> str:
    if case["polarity"] != "positive":
        return "negative_control"
    primary_id = case["primary_entry_ids"][0]
    if primary_id not in diagnostic["candidate_ids_top20"]:
        return "candidate_generation_top20"
    if primary_id not in production["post_gate_ids"]:
        return "relevance_gate"
    if primary_id not in production["post_consolidation_ids"]:
        return "candidate_consolidator"
    if production["controller_mode"] == "none":
        return "controller_disabled"
    if primary_id not in production["rendered_ids"]:
        return "hard_budget"
    return "rendered"


class NetworkGuard:
    def __init__(self) -> None:
        self.call_count = 0

    def blocked(self, *_args: Any, **_kwargs: Any) -> None:
        self.call_count += 1
        raise AssertionError("network access is forbidden in the Phase 3A evaluator")


def evaluate_case(case: dict[str, Any], background: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.perf_counter()
    with isolated_case_manager(case, background) as (manager, temporary_root), patch(
        "minicode.memory.time.time", return_value=REFERENCE_TIME
    ), patch("minicode.memory_retrieval.time.time", return_value=REFERENCE_TIME), patch(
        "minicode.memory_pipeline.time.time", return_value=REFERENCE_TIME
    ), patch("minicode.memory_injector.time.time", return_value=REFERENCE_TIME):
        counters_before = _entry_counter_snapshot(manager)
        files_before = snapshot_isolated_case_tree(temporary_root)
        manager_arm = _manager_arm(manager, case)
        _, diagnostic = _diagnostic_arm(manager, case)
        counters_after_diagnostic = _entry_counter_snapshot(manager)
        files_after_diagnostic = snapshot_isolated_case_tree(temporary_root)
        _, production = _production_arm(manager, case)
        counters_after_production = _entry_counter_snapshot(manager)
        local_files_after = snapshot_isolated_case_tree(temporary_root)
    primary_id = case["primary_entry_ids"][0] if case["primary_entry_ids"] else ""
    result = {
        "case_id": case["case_id"],
        "split": case["split"],
        "polarity": case["polarity"],
        "category": case["category"],
        "semantic_relation_type": case["semantic_relation_type"],
        "language_direction": f"{case['query_language']}->{case['memory_language']}",
        "scope": case["expected_scope"] or "none",
        "lexical_overlap_class": case["lexical_overlap_class"],
        "normalized_token_overlap": case["normalized_token_overlap"],
        "current_files_present": bool(case["current_files"]),
        "domain_present": bool(case["active_domains"]),
        "primary_entry_ids": list(case["primary_entry_ids"]),
        "must_exclude_ids": list(case["must_exclude_ids"]),
        "hard_negative_control_case_ids": list(case["hard_negative_control_case_ids"]),
        "manager_global_search": manager_arm,
        "canonical_diagnostic": diagnostic,
        "canonical_production": production,
        "primary_manager_rank": _rank(primary_id, manager_arm["candidate_ids_top50"]) if primary_id else None,
        "primary_diagnostic_rank": _rank(primary_id, diagnostic["candidate_ids_top50"]) if primary_id else None,
        "first_loss_stage": _first_loss(case, diagnostic, production),
        "diagnostic_side_effects": {
            "counters_unchanged": counters_before == counters_after_diagnostic,
            "filesystem_unchanged": files_before == files_after_diagnostic,
            "scope_saves": 0,
        },
        "production_counter_state_changed": counters_after_diagnostic != counters_after_production,
        "temporary_file_count": local_files_after["file_count"],
        "latency_ms": round((time.perf_counter() - started) * 1000, 6),
    }
    return result


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total <= 0:
        return [0.0, 0.0]
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    return [round(max(0.0, center - margin), 6), round(min(1.0, center + margin), 6)]


def _slice_metrics(
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    arm_name: str = "canonical_diagnostic",
) -> dict[str, Any]:
    by_id = {case["case_id"]: case for case in cases}
    positives = [result for result in results if result["polarity"] == "positive"]
    negatives = [result for result in results if result["polarity"] == "hard_negative"]
    hits: dict[int, int] = {cutoff: 0 for cutoff in (1, 3, 5, 10, 20)}
    reciprocal_ranks: list[float] = []
    ndcg_values: list[float] = []
    post_gate_hits = 0
    post_consolidation_hits = 0
    rendered_hits = 0
    total_rendered = 0
    relevant_rendered = 0
    negative_candidate_cases = 0
    negative_post_gate_cases = 0
    negative_rendered_cases = 0
    forbidden_candidate_entries = 0
    forbidden_candidate_hits = 0
    allowed_candidate_entries = 0
    allowed_candidate_hits = 0
    lifecycle_safety_leakage = 0
    for result in positives:
        primary_id = result["primary_entry_ids"][0]
        arm = result[arm_name]
        top50 = arm.get("candidate_ids_top50", [])
        rank = _rank(primary_id, top50)
        for cutoff in hits:
            hits[cutoff] += bool(rank is not None and rank <= cutoff)
        reciprocal_ranks.append(1.0 / rank if rank is not None and rank <= 20 else 0.0)
        ndcg_values.append(
            1.0 / math.log2(rank + 1) if rank is not None and rank <= 5 else 0.0
        )
        production = result["canonical_production"]
        post_gate_hits += primary_id in production["post_gate_ids"]
        post_consolidation_hits += primary_id in production["post_consolidation_ids"]
        rendered_hits += primary_id in production["rendered_ids"]
    for result in results:
        case = by_id[result["case_id"]]
        rendered = set(result["canonical_production"]["rendered_ids"])
        primary = set(case["primary_entry_ids"])
        total_rendered += len(rendered)
        relevant_rendered += len(rendered & primary)
    for result in negatives:
        case = by_id[result["case_id"]]
        excluded = set(case["must_exclude_ids"])
        candidate = set(result[arm_name].get("candidate_ids_top20", []))
        post_gate = set(result["canonical_production"]["post_gate_ids"])
        rendered = set(result["canonical_production"]["rendered_ids"])
        negative_candidate_cases += bool(excluded & candidate)
        negative_post_gate_cases += bool(excluded & post_gate)
        negative_rendered_cases += bool(excluded & rendered)
        if case["allow_wide_candidate"]:
            allowed_candidate_entries += len(excluded)
            allowed_candidate_hits += len(excluded & candidate)
        else:
            forbidden_candidate_entries += len(excluded)
            forbidden_candidate_hits += len(excluded & candidate)
            lifecycle_safety_leakage += len(excluded & (candidate | post_gate | rendered))
    positive_count = len(positives)
    negative_count = len(negatives)
    counts = {
        "cases": len(results),
        "positive_cases": positive_count,
        "hard_negative_cases": negative_count,
        "primary_entries": positive_count,
        **{f"candidate_hits_at_{cutoff}": hits[cutoff] for cutoff in hits},
        "post_gate_primary_hits": post_gate_hits,
        "post_consolidation_primary_hits": post_consolidation_hits,
        "rendered_primary_hits": rendered_hits,
        "total_rendered_entries": total_rendered,
        "relevant_rendered_entries": relevant_rendered,
        "hard_negative_candidate_cases": negative_candidate_cases,
        "hard_negative_post_gate_cases": negative_post_gate_cases,
        "hard_negative_rendered_cases": negative_rendered_cases,
        "allowed_wide_excluded_entries": allowed_candidate_entries,
        "allowed_wide_candidate_hits": allowed_candidate_hits,
        "forbidden_excluded_entries": forbidden_candidate_entries,
        "forbidden_candidate_hits": forbidden_candidate_hits,
        "lifecycle_safety_leakage_entries": lifecycle_safety_leakage,
    }
    metrics: dict[str, Any] = {
        **{
            f"candidate_recall_at_{cutoff}": hits[cutoff] / positive_count
            if positive_count
            else 1.0
            for cutoff in hits
        },
        "primary_candidate_hit_rate_at_20": hits[20] / positive_count if positive_count else 1.0,
        "mrr_at_20": _mean(reciprocal_ranks),
        "ndcg_at_5": _mean(ndcg_values),
        "post_gate_recall": post_gate_hits / positive_count if positive_count else 1.0,
        "post_consolidation_recall": post_consolidation_hits / positive_count
        if positive_count
        else 1.0,
        "rendered_recall": rendered_hits / positive_count if positive_count else 1.0,
        "rendered_precision": relevant_rendered / total_rendered if total_rendered else 1.0,
        "hard_negative_candidate_rate": negative_candidate_cases / negative_count
        if negative_count
        else 0.0,
        "hard_negative_post_gate_rate": negative_post_gate_cases / negative_count
        if negative_count
        else 0.0,
        "hard_negative_rendered_rate": negative_rendered_cases / negative_count
        if negative_count
        else 0.0,
        "negative_false_injection_rate": negative_rendered_cases / negative_count
        if negative_count
        else 0.0,
        "allowed_wide_candidate_noise_rate": allowed_candidate_hits / allowed_candidate_entries
        if allowed_candidate_entries
        else 0.0,
        "forbidden_false_candidate_rate": forbidden_candidate_hits / forbidden_candidate_entries
        if forbidden_candidate_entries
        else 0.0,
    }
    metrics["candidate_recall_at_20_wilson_95"] = wilson_interval(hits[20], positive_count)
    return {"counts": counts, "metrics": metrics}


def _manager_metrics(cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    projected: list[dict[str, Any]] = []
    for result in results:
        clone = copy.deepcopy(result)
        clone["manager_global_search"]["post_gate_ids"] = []
        clone["manager_global_search"]["post_consolidation_ids"] = []
        clone["manager_global_search"]["rendered_ids"] = []
        projected.append(clone)
    metrics = _slice_metrics(cases, projected, arm_name="manager_global_search")
    for key in (
        "post_gate_recall",
        "post_consolidation_recall",
        "rendered_recall",
        "rendered_precision",
        "hard_negative_post_gate_rate",
        "hard_negative_rendered_rate",
        "negative_false_injection_rate",
    ):
        metrics["metrics"].pop(key, None)
    return metrics


def _dimension_groups(cases: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    groups: dict[str, dict[str, list[str]]] = {
        name: defaultdict(list)
        for name in (
            "category",
            "polarity",
            "language_direction",
            "scope",
            "lexical_overlap_bucket",
            "semantic_relation_type",
            "current_files",
            "active_domains",
            "split",
        )
    }
    for case in cases:
        values = {
            "category": case["category"],
            "polarity": case["polarity"],
            "language_direction": f"{case['query_language']}->{case['memory_language']}",
            "scope": case["expected_scope"] or "none",
            "lexical_overlap_bucket": case["lexical_overlap_class"],
            "semantic_relation_type": case["semantic_relation_type"],
            "current_files": "present" if case["current_files"] else "absent",
            "active_domains": "present" if case["active_domains"] else "absent",
            "split": case["split"],
        }
        for dimension, value in values.items():
            groups[dimension][value].append(case["case_id"])
    return {name: dict(values) for name, values in groups.items()}


def dimension_metrics(
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    cases_by_id = {case["case_id"]: case for case in cases}
    results_by_id = {result["case_id"]: result for result in results}
    output: dict[str, Any] = {}
    for dimension, values in _dimension_groups(cases).items():
        output[dimension] = {}
        for value, case_ids in sorted(values.items()):
            subset_cases = [cases_by_id[case_id] for case_id in case_ids]
            subset_results = [results_by_id[case_id] for case_id in case_ids]
            output[dimension][value] = _slice_metrics(subset_cases, subset_results)
    return output


def _primary_spec(case: dict[str, Any]) -> dict[str, Any] | None:
    if not case["primary_entry_ids"]:
        return None
    primary_id = case["primary_entry_ids"][0]
    return next(entry for entry in case["memories"] if entry["id"] == primary_id)


def semantic_gap_adjudication(
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    by_result = {result["case_id"]: result for result in results}
    controls = {
        case["case_id"]: case for case in cases if case["polarity"] == "hard_negative"
    }
    confirmed: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for case in cases:
        if case["polarity"] != "positive":
            continue
        result = by_result[case["case_id"]]
        primary = _primary_spec(case)
        if primary is None:
            continue
        reasons: list[str] = []
        eligible = (
            primary["approval_status"] == "approved"
            and primary["lifecycle_status"] == "active"
            and primary["safety_status"] == "safe"
            and not primary["curator_locked"]
            and primary["tier"] != "archival"
        )
        if not eligible:
            reasons.append("primary_not_injectable")
        if not case["query_is_sufficient"]:
            reasons.append("query_underspecified")
        if not case["structured_metadata_complete"]:
            reasons.append("structured_metadata_incomplete")
        if not case["hard_negative_control_case_ids"]:
            reasons.append("missing_hard_negative_control")
        elif not all(control_id in controls for control_id in case["hard_negative_control_case_ids"]):
            reasons.append("invalid_hard_negative_control")
        if result["primary_diagnostic_rank"] is not None and result["primary_diagnostic_rank"] <= 20:
            reasons.append("primary_entered_diagnostic_top20")
        if case["category"] == "file_module_rename":
            reasons.append("structured_rename_relation_is_primary_evidence")
        if result["first_loss_stage"] != "candidate_generation_top20":
            reasons.append(f"first_loss_is_{result['first_loss_stage']}")
        item = {
            "case_id": case["case_id"],
            "split": case["split"],
            "category": case["category"],
            "primary_id": primary["id"],
            "language_direction": result["language_direction"],
            "scope": primary["scope"],
            "lexical_overlap_bucket": case["lexical_overlap_class"],
            "normalized_token_overlap": case["normalized_token_overlap"],
            "first_failure_stage": result["first_loss_stage"],
            "diagnostic_rank": result["primary_diagnostic_rank"],
            "why_semantic_gap": case["why_semantically_relevant"],
            "why_lexical_may_fail": case["why_lexical_retrieval_may_fail"],
            "why_not_metadata_scope_lifecycle_budget": (
                "Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget."
            ),
            "hard_negative_controls": list(case["hard_negative_control_case_ids"]),
            "future_value": case["miss_impact"],
            "synthetic_query_excerpt": case["query"][:180],
        }
        if reasons:
            item["exclusion_reasons"] = reasons
            excluded.append(item)
        else:
            confirmed.append(item)
    by_split = {
        split: [item for item in confirmed if item["split"] == split]
        for split in ("analysis", "sealed")
    }
    non_semantic_candidate_misses = [
        item
        for item in excluded
        if item["first_failure_stage"] == "candidate_generation_top20"
    ]
    downstream_or_success = [
        item
        for item in excluded
        if item["first_failure_stage"] != "candidate_generation_top20"
    ]
    return {
        "strict_definition": {
            "diagnostic_cutoff": 20,
            "requires_injectable_primary": True,
            "requires_sufficient_query": True,
            "requires_complete_structured_metadata": True,
            "requires_hard_negative_control": True,
            "file_rename_metadata_primary_cases_excluded": True,
        },
        "confirmed_count": len(confirmed),
        "confirmed_analysis_count": len(by_split["analysis"]),
        "confirmed_sealed_count": len(by_split["sealed"]),
        "confirmed_category_count": len({item["category"] for item in confirmed}),
        "confirmed_language_directions": sorted(
            {item["language_direction"] for item in confirmed}
        ),
        "confirmed": confirmed,
        "not_confirmed": excluded,
        "non_semantic_candidate_misses": non_semantic_candidate_misses,
        "downstream_or_success_cases": downstream_or_success,
    }


def stage_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    stages = Counter(
        result["first_loss_stage"]
        for result in results
        if result["polarity"] == "positive"
    )
    disagreements = {
        "rendered_vs_injection_counter": 0,
        "rendered_vs_feedback": 0,
        "selected_vs_retrieval_counter": 0,
    }
    total_tokens = 0
    total_saves = 0
    queryless_no_match = 0
    for result in results:
        production = result["canonical_production"]
        disagreements["rendered_vs_injection_counter"] += (
            production["rendered_ids"] != production["injection_counter_ids"]
        )
        disagreements["rendered_vs_feedback"] += (
            production["rendered_ids"] != production["feedback_ids"]
        )
        disagreements["selected_vs_retrieval_counter"] += (
            production["post_consolidation_ids"] != production["retrieval_counter_ids"]
        )
        total_tokens += production["total_tokens"]
        total_saves += production["total_scope_saves"]
        queryless_no_match += production["no_match_reason"].startswith("queryless")
    return {
        "first_loss_counts": dict(sorted(stages.items())),
        "candidate_miss_count": stages["candidate_generation_top20"],
        "gate_rejection_count": stages["relevance_gate"],
        "incorrect_consolidation_suppression_count": stages["candidate_consolidator"],
        "controller_disabled_count": stages["controller_disabled"],
        "budget_only_drop_count": stages["hard_budget"],
        "rendered_count": stages["rendered"],
        "queryless_no_match_count": queryless_no_match,
        "id_disagreements": disagreements,
        "total_prompt_tokens": total_tokens,
        "average_prompt_tokens_per_case": total_tokens / len(results) if results else 0.0,
        "total_scope_saves": total_saves,
        "average_scope_saves_per_case": total_saves / len(results) if results else 0.0,
        "diagnostic_counter_side_effect_cases": sum(
            not result["diagnostic_side_effects"]["counters_unchanged"] for result in results
        ),
        "diagnostic_filesystem_side_effect_cases": sum(
            not result["diagnostic_side_effects"]["filesystem_unchanged"]
            for result in results
        ),
    }


@contextmanager
def _isolated_benchmark_manager(count: int) -> Iterator[Any]:
    with tempfile.TemporaryDirectory(prefix="minicode-phase3a-benchmark-") as temporary:
        root = Path(temporary)
        home = root / "home"
        workspace = root / "workspace"
        home.mkdir()
        workspace.mkdir()
        with patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
            from minicode import memory as memory_module
            from minicode.memory import MemoryEntry, MemoryManager, MemoryScope, MemoryTier

            with patch.object(memory_module, "MINI_CODE_DIR", home / ".mini-code"):
                manager = MemoryManager(project_root=workspace)
            for index in range(count):
                entry = MemoryEntry(
                    id=f"sg-benchmark-{count}-{index:04d}",
                    scope=MemoryScope.PROJECT,
                    category="recovery",
                    content=(
                        f"Synthetic checkpoint recovery policy for component {index:04d} "
                        f"retains receipt generation {index % 17}."
                    ),
                    created_at=REFERENCE_TIME - 86400,
                    updated_at=REFERENCE_TIME - 3600,
                    tags=["checkpoint", "recovery", f"component-{index:04d}"],
                    domains=["benchmark"],
                    tier=MemoryTier.LONG_TERM,
                    source="semantic_gap_benchmark",
                    provenance={"kind": "synthetic_benchmark"},
                )
                manager.memories[entry.scope].entries.append(entry)
            for memory_file in manager.memories.values():
                memory_file._rebuild_indices()
            yield manager


def benchmark_retrieval() -> dict[str, Any]:
    from minicode.memory_retrieval import (
        CanonicalMemoryRetriever,
        MemoryRetrievalRequest,
        RetrievalSource,
    )

    output: dict[str, Any] = {}
    for count, repetitions in ((100, 10), (500, 5), (1000, 3)):
        manager_samples: list[float] = []
        retrieval_samples: list[float] = []
        evaluator_samples: list[float] = []
        with _isolated_benchmark_manager(count) as manager, patch(
            "minicode.memory.time.time", return_value=REFERENCE_TIME
        ), patch("minicode.memory_retrieval.time.time", return_value=REFERENCE_TIME):
            retriever = CanonicalMemoryRetriever(manager)
            request = MemoryRetrievalRequest(
                query="checkpoint recovery policy component",
                active_domains=("benchmark",),
                context_usage=0.0,
                max_memories=20,
                max_total_tokens=8000,
                max_tokens_per_memory=300,
                min_relevance=0.0,
                source_entrypoint=RetrievalSource.CANONICAL,
            )
            retriever.retrieve(request)
            for _ in range(repetitions):
                started = time.perf_counter()
                manager.search(
                    request.query,
                    scope=None,
                    limit=50,
                    min_relevance=0.0,
                    active_domains=list(request.active_domains),
                    record_usage=False,
                )
                manager_samples.append((time.perf_counter() - started) * 1000)
                started = time.perf_counter()
                result = retriever.retrieve(request)
                retrieval_samples.append((time.perf_counter() - started) * 1000)
                started = time.perf_counter()
                _arm_view(result)
                evaluator_samples.append((time.perf_counter() - started) * 1000)
            tracemalloc.start()
            result = retriever.retrieve(request)
            _arm_view(result)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        output[str(count)] = {
            "candidate_count": count,
            "repetitions": repetitions,
            "manager_search_p50_ms": statistics.median(manager_samples),
            "manager_search_p95_ms": _percentile(manager_samples, 0.95),
            "canonical_retrieval_p50_ms": statistics.median(retrieval_samples),
            "canonical_retrieval_p95_ms": _percentile(retrieval_samples, 0.95),
            "evaluator_projection_p50_ms": statistics.median(evaluator_samples),
            "evaluator_projection_p95_ms": _percentile(evaluator_samples, 0.95),
            "peak_memory_bytes": peak,
            "canonical_candidate_count": len(result.candidate_ids),
            "post_consolidation_count": len(result.selected_ids),
            "candidate_consolidator_cap": 256,
            "cap_preserved": len(result.selected_ids) <= 256,
        }
    return {
        "scales": output,
        "complexity_note": (
            "Manager scoring and canonical ranking scan all active entries; sorting is O(N log N). "
            "Post-gate CandidateConsolidator remains bounded to 256 before pairwise work."
        ),
    }


def _phase2b_regression_view(project_root: Path) -> dict[str, Any]:
    path = project_root / "artifacts" / "memory-retrieval-phase2b.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    phase2a = document.get("phase2a_80_case", {})
    holdout = document.get("holdout", {})
    return {
        "frozen_acceptance_passed": bool(document.get("acceptance_passed")),
        "phase2a_metrics": {
            name: phase2a.get("metrics", {}).get(name)
            for name in (
                "precision_at_1",
                "recall_at_5",
                "primary_hit_rate",
                "rendered_precision",
                "must_exclude_violation_rate",
                "negative_false_injection_rate",
            )
        },
        "holdout_gates_passed": all(holdout.get("gates", {}).values()),
        "interpretation": (
            "Phase 3A does not alter Gate or CandidateConsolidator. Frozen Phase 2B "
            "acceptance remains the regression authority; Phase 3A stage drops are diagnostic observations."
        ),
    }


def _safe_report_scan(report: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    findings = {
        "private_key_block": "BEGIN PRIVATE KEY" in encoded,
        "openai_key_shape": bool(re.search(r"\bsk-[A-Za-z0-9_-]{20,}\b", encoded)),
        "aws_access_key_shape": bool(re.search(r"\bAKIA[0-9A-Z]{16}\b", encoded)),
        "jwt_shape": bool(
            re.search(
                r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
                encoded,
            )
        ),
        "absolute_user_path": bool(re.search(r"/Users/[^/\s\"]+", encoded)),
    }
    return {"passed": not any(findings.values()), "findings": findings}


def deterministic_case_view(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    view = copy.deepcopy(results)
    for result in view:
        result.pop("latency_ms", None)
        for arm_name in (
            "manager_global_search",
            "canonical_diagnostic",
            "canonical_production",
        ):
            result[arm_name].pop("latency_ms", None)
    return view


def semantic_behavior_view(report: dict[str, Any]) -> dict[str, Any]:
    """Project only deterministic retrieval semantics for v1/v2/v3/v4 certification.

    Source hashes, formal-tree metadata, performance, timings, paths, process
    state, and the certification-dependent Phase 3B gate are intentionally
    absent. The accepted v1 artifact remains the gold document.
    """
    dataset = report["dataset"]
    return {
        "dataset": {
            key: copy.deepcopy(dataset[key])
            for key in (
                "dataset_id",
                "case_count",
                "positive_count",
                "hard_negative_count",
                "analysis_count",
                "sealed_count",
                "positive_categories",
                "negative_categories",
                "freeze",
            )
        },
        "arms": copy.deepcopy(report["arms"]),
        "overall_metrics": copy.deepcopy(report["overall_metrics"]),
        "sealed_metrics": copy.deepcopy(report["sealed_metrics"]),
        "stage_attribution": copy.deepcopy(report["stage_attribution"]),
        "sealed_stage_attribution": copy.deepcopy(
            report["sealed_stage_attribution"]
        ),
        "semantic_gap_adjudication": copy.deepcopy(
            report["semantic_gap_adjudication"]
        ),
        "phase2b_regression": copy.deepcopy(report["phase2b_regression"]),
        "io_and_feedback": copy.deepcopy(report["io_and_feedback"]),
        "remote_call_count": report["remote_call_count"],
        "per_case_results": deterministic_case_view(report["per_case_results"]),
    }


def semantic_behavior_fingerprint(report: dict[str, Any]) -> str:
    """Return the accepted v1-compatible deterministic per-case fingerprint."""
    encoded = json.dumps(
        semantic_behavior_view(report)["per_case_results"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def semantic_behavior_projection_fingerprint(report: dict[str, Any]) -> str:
    """Hash the complete deterministic semantic certification projection."""
    encoded = json.dumps(
        semantic_behavior_view(report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def evaluate_fingerprint(dataset_root: Path) -> str:
    dataset = load_dataset(dataset_root)
    network_guard = NetworkGuard()
    with patch.object(socket, "create_connection", network_guard.blocked), patch.object(
        socket.socket, "connect", network_guard.blocked
    ):
        results = [
            evaluate_case(case, dataset["background"]) for case in dataset["cases"]
        ]
    if network_guard.call_count:
        raise AssertionError("fingerprint evaluation attempted network access")
    encoded = json.dumps(
        deterministic_case_view(results),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _phase3b_gate(
    sealed_metrics: dict[str, Any],
    sealed_stages: dict[str, Any],
    gaps: dict[str, Any],
    audit_passed: bool,
) -> dict[str, Any]:
    metrics = sealed_metrics["metrics"]
    sealed_confirmed = [item for item in gaps["confirmed"] if item["split"] == "sealed"]
    candidate_misses = sealed_stages["candidate_miss_count"]
    later_losses = sum(
        sealed_stages[name]
        for name in (
            "gate_rejection_count",
            "incorrect_consolidation_suppression_count",
            "controller_disabled_count",
            "budget_only_drop_count",
        )
    )
    gates = {
        "sealed_diagnostic_recall_at_20_below_0_90": metrics["candidate_recall_at_20"] < 0.90,
        "sealed_confirmed_gaps_at_least_12": len(sealed_confirmed) >= 12,
        "sealed_confirmed_categories_at_least_3": len(
            {item["category"] for item in sealed_confirmed}
        )
        >= 3,
        "language_and_template_diversity": (
            len({item["language_direction"] for item in sealed_confirmed}) >= 2
            and len({item["category"] for item in sealed_confirmed}) >= 3
        ),
        "hard_negative_widening_noise_observed": (
            metrics["allowed_wide_candidate_noise_rate"] > 0
            or metrics["hard_negative_candidate_rate"] > 0
        ),
        "later_stages_not_main_loss": candidate_misses > later_losses,
        "data_and_annotation_audit_passed": audit_passed,
    }
    if all(gates.values()):
        decision = "enter_offline_phase3b_prototype_only"
    elif metrics["candidate_recall_at_20"] >= 0.95 or len(sealed_confirmed) < 8:
        decision = "stop_no_embedding_recommendation"
    else:
        decision = "inconclusive_expand_independent_holdout"
    return {
        "split": "sealed",
        "gates": gates,
        "passed": all(gates.values()),
        "decision": decision,
        "sealed_confirmed_gap_count": len(sealed_confirmed),
        "sealed_confirmed_categories": sorted(
            {item["category"] for item in sealed_confirmed}
        ),
        "direct_production_enablement_allowed": False,
    }


def evaluate_semantic_gap(
    *,
    project_root: Path,
    dataset_root: Path,
    formal_root: Path | None = None,
    stage_start_snapshot_path: Path | None = None,
) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    dataset_root = Path(dataset_root).resolve()
    formal_root = Path(formal_root or (Path.home() / ".mini-code")).resolve()
    formal_before = snapshot_tree(formal_root)
    dataset = load_dataset(dataset_root)
    dataset_before = verify_frozen_dataset(dataset_root)
    frozen_before = {
        "production": hash_paths(project_root, PRODUCTION_RETRIEVAL_HASHES),
        "phase1": hash_paths(project_root, PHASE1_FROZEN_HASHES),
        "phase2a": hash_paths(project_root, PHASE2A_FROZEN_HASHES),
        "phase2b": hash_paths(project_root, PHASE2B_FROZEN_HASHES),
    }
    production_baseline_before = verify_active_baseline(project_root=project_root)
    start_snapshot: dict[str, Any] | None = None
    start_snapshot_sha: str | None = None
    if stage_start_snapshot_path is not None and Path(stage_start_snapshot_path).is_file():
        start_snapshot_path = Path(stage_start_snapshot_path)
        start_snapshot_sha = sha256_file(start_snapshot_path)
        start_snapshot = json.loads(start_snapshot_path.read_text(encoding="utf-8"))
    network_guard = NetworkGuard()
    tracemalloc.start()
    with patch.object(socket, "create_connection", network_guard.blocked), patch.object(
        socket.socket, "connect", network_guard.blocked
    ):
        results = [
            evaluate_case(case, dataset["background"]) for case in dataset["cases"]
        ]
    _, evaluation_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    with patch.object(socket, "create_connection", network_guard.blocked), patch.object(
        socket.socket, "connect", network_guard.blocked
    ):
        performance = benchmark_retrieval()
    overall = _slice_metrics(dataset["cases"], results)
    manager = _manager_metrics(dataset["cases"], results)
    dimensions = dimension_metrics(dataset["cases"], results)
    stages = stage_metrics(results)
    gaps = semantic_gap_adjudication(dataset["cases"], results)
    sealed_case_ids = {
        case["case_id"] for case in dataset["cases"] if case["split"] == "sealed"
    }
    sealed_cases = [case for case in dataset["cases"] if case["case_id"] in sealed_case_ids]
    sealed_results = [result for result in results if result["case_id"] in sealed_case_ids]
    sealed_metrics = _slice_metrics(sealed_cases, sealed_results)
    sealed_stages = stage_metrics(sealed_results)
    formal_after = snapshot_tree(formal_root)
    dataset_after = verify_frozen_dataset(dataset_root)
    frozen_after = {
        "production": hash_paths(project_root, PRODUCTION_RETRIEVAL_HASHES),
        "phase1": hash_paths(project_root, PHASE1_FROZEN_HASHES),
        "phase2a": hash_paths(project_root, PHASE2A_FROZEN_HASHES),
        "phase2b": hash_paths(project_root, PHASE2B_FROZEN_HASHES),
    }
    production_baseline_after = verify_active_baseline(project_root=project_root)
    integrity_gates = {
        "formal_tree_unchanged_during_evaluation": formal_before == formal_after,
        "stage_start_matches_pre_evaluation": start_snapshot is None or start_snapshot == formal_before,
        "dataset_freeze_unchanged": dataset_before == dataset_after and dataset_after["matches"],
        "production_retrieval_files_unchanged": (
            frozen_before["production"]["matches"]
            and frozen_after["production"]["matches"]
            and production_baseline_before["matches"]
            and production_baseline_after["matches"]
            and production_baseline_before == production_baseline_after
        ),
        "phase1_assets_unchanged": frozen_before["phase1"]["matches"] and frozen_after["phase1"]["matches"],
        "phase2a_assets_unchanged": frozen_before["phase2a"]["matches"] and frozen_after["phase2a"]["matches"],
        "phase2b_assets_unchanged": frozen_before["phase2b"]["matches"] and frozen_after["phase2b"]["matches"],
        "diagnostic_zero_counter_side_effects": stages["diagnostic_counter_side_effect_cases"] == 0,
        "diagnostic_zero_filesystem_side_effects": stages["diagnostic_filesystem_side_effect_cases"] == 0,
        "zero_network_calls": network_guard.call_count == 0,
    }
    audit_passed = all(integrity_gates.values())
    report: dict[str, Any] = {
        "schema_version": "memory-retrieval-semantic-gap-baseline-v1",
        "evaluator_version": EVALUATOR_VERSION,
        "reference_time": int(REFERENCE_TIME),
        "synthetic_data": True,
        "evaluation_passed": audit_passed,
        "dataset": {
            "dataset_id": dataset["manifest"]["dataset_id"],
            "case_count": len(dataset["cases"]),
            "positive_count": sum(case["polarity"] == "positive" for case in dataset["cases"]),
            "hard_negative_count": sum(
                case["polarity"] == "hard_negative" for case in dataset["cases"]
            ),
            "analysis_count": sum(case["split"] == "analysis" for case in dataset["cases"]),
            "sealed_count": sum(case["split"] == "sealed" for case in dataset["cases"]),
            "positive_categories": dataset["manifest"]["positive_categories"],
            "negative_categories": dataset["manifest"]["negative_categories"],
            "freeze": dataset_before,
        },
        "arms": {
            "manager_global_search": {
                "definition": "MemoryManager.search across USER/PROJECT/LOCAL, limit=50, min_relevance=0, record_usage=False.",
                "metrics": manager,
            },
            "canonical_diagnostic": {
                "definition": "Canonical retriever, low pressure, min_relevance=0, 20-memory/8000-token render budget; candidate ranks observed through top 50 without counters.",
                "metrics": overall,
            },
            "canonical_production": {
                "definition": "MemoryPipeline.inject with current defaults (5 memories, 200 tokens each, min relevance 0.3), followed by rendered-only success feedback in an isolated HOME.",
                "metrics": {
                    key: value
                    for key, value in overall["metrics"].items()
                    if key.startswith("post_")
                    or key.startswith("rendered_")
                    or key.startswith("negative_")
                    or key.startswith("hard_negative_rendered")
                },
            },
        },
        "overall_metrics": overall,
        "sealed_metrics": sealed_metrics,
        "dimension_metrics": dimensions,
        "stage_attribution": stages,
        "sealed_stage_attribution": sealed_stages,
        "semantic_gap_adjudication": gaps,
        "phase2b_regression": _phase2b_regression_view(project_root),
        "io_and_feedback": {
            "stage_metrics": stages,
            "diagnostic_read_only": (
                stages["diagnostic_counter_side_effect_cases"] == 0
                and stages["diagnostic_filesystem_side_effect_cases"] == 0
            ),
            "rendered_injection_feedback_identity": all(
                value == 0
                for key, value in stages["id_disagreements"].items()
                if key != "selected_vs_retrieval_counter"
            ),
            "retrieval_counter_semantics": "post-consolidation selected IDs",
            "injection_counter_semantics": "rendered IDs only",
            "feedback_semantics": "rendered IDs only",
        },
        "performance": {
            **performance,
            "evaluation_peak_memory_bytes": evaluation_peak,
            "case_latency_ms": {
                "p50": statistics.median(result["latency_ms"] for result in results),
                "p95": _percentile([result["latency_ms"] for result in results], 0.95),
                "max": max(result["latency_ms"] for result in results),
            },
        },
        "remote_call_count": network_guard.call_count,
        "integrity": {
            "gates": integrity_gates,
            "formal_before": tree_summary(formal_before),
            "formal_after": tree_summary(formal_after),
            "stage_start_snapshot_available": start_snapshot is not None,
            "stage_start_snapshot_sha256": start_snapshot_sha,
            "dataset_before": dataset_before,
            "dataset_after": dataset_after,
            "frozen_before": frozen_before,
            "frozen_after": frozen_after,
            "production_baseline_before": production_baseline_before,
            "production_baseline_after": production_baseline_after,
        },
        "per_case_results": results,
        "limitations": [
            "This is a synthetic pressure suite, not a random sample of production tasks; Wilson intervals describe only this fixture.",
            "Candidate Recall@20 treats rank 21+ as a miss even though the current canonical diagnostics retain the full active candidate tuple internally.",
            "File/module rename cases are excluded from confirmed semantic gaps when structured rename provenance is the primary relevance evidence.",
            "No embedding, vector database, LLM, remote reranker, query rewrite, synonym patch, or case-specific production rule is evaluated.",
            "Latency and peak memory are machine-dependent and excluded from deterministic fingerprints.",
        ],
    }
    report["phase3b_entry_gate"] = _phase3b_gate(
        sealed_metrics,
        sealed_stages,
        gaps,
        audit_passed,
    )
    report["artifact_security_scan"] = _safe_report_scan(report)
    report["evaluation_passed"] = report["evaluation_passed"] and report[
        "artifact_security_scan"
    ]["passed"]
    return report


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def render_baseline_markdown(report: dict[str, Any]) -> str:
    overall = report["overall_metrics"]
    metrics = overall["metrics"]
    counts = overall["counts"]
    manager = report["arms"]["manager_global_search"]["metrics"]["metrics"]
    sealed = report["sealed_metrics"]["metrics"]
    stages = report["stage_attribution"]
    gate = report["phase3b_entry_gate"]
    lines = [
        "# Memory Retrieval Semantic Gap Baseline",
        "",
        "> Frozen synthetic Retrieval Phase 3A evaluation. These values are not production traffic estimates.",
        "",
        "## Acceptance",
        "",
        f"- Evaluation integrity passed: `{report['evaluation_passed']}`",
        "- Production code modified by Phase 3A: `False`",
        f"- Remote calls: `{report['remote_call_count']}`",
        f"- Dataset: `{report['dataset']['case_count']}` cases (`{report['dataset']['positive_count']}` positive, `{report['dataset']['hard_negative_count']}` hard negative)",
        f"- Freeze manifest SHA-256: `{report['dataset']['freeze']['manifest_sha256']}`",
        "",
        "## Retrieval Arms",
        "",
        f"1. Manager global search: {report['arms']['manager_global_search']['definition']}",
        f"2. Canonical diagnostic: {report['arms']['canonical_diagnostic']['definition']}",
        f"3. Canonical production: {report['arms']['canonical_production']['definition']}",
        "",
        "## Candidate Metrics",
        "",
        "| Metric | Manager | Canonical diagnostic | Sealed diagnostic | Raw diagnostic count |",
        "|---|---:|---:|---:|---:|",
    ]
    for cutoff in (1, 3, 5, 10, 20):
        key = f"candidate_recall_at_{cutoff}"
        lines.append(
            f"| Recall@{cutoff} | {_percent(manager[key])} | {_percent(metrics[key])} | "
            f"{_percent(sealed[key])} | {counts[f'candidate_hits_at_{cutoff}']}/{counts['primary_entries']} |"
        )
    lines.extend(
        [
            f"| MRR@20 | {manager['mrr_at_20']:.4f} | {metrics['mrr_at_20']:.4f} | {sealed['mrr_at_20']:.4f} | - |",
            f"| NDCG@5 | {manager['ndcg_at_5']:.4f} | {metrics['ndcg_at_5']:.4f} | {sealed['ndcg_at_5']:.4f} | - |",
            "",
            "## Downstream And Negative Metrics",
            "",
            f"- Post-Gate recall: `{_percent(metrics['post_gate_recall'])}`.",
            f"- Post-Consolidation recall: `{_percent(metrics['post_consolidation_recall'])}`.",
            f"- Rendered recall / precision: `{_percent(metrics['rendered_recall'])}` / `{_percent(metrics['rendered_precision'])}`.",
            f"- Hard-negative candidate / post-Gate / rendered rates: `{_percent(metrics['hard_negative_candidate_rate'])}` / `{_percent(metrics['hard_negative_post_gate_rate'])}` / `{_percent(metrics['hard_negative_rendered_rate'])}`.",
            f"- Forbidden candidate leakage entries: `{counts['lifecycle_safety_leakage_entries']}`.",
            "",
            "## First Loss",
            "",
        ]
    )
    for name, count in stages["first_loss_counts"].items():
        lines.append(f"- `{name}`: {count}")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Confirmed semantic gaps: `{report['semantic_gap_adjudication']['confirmed_count']}` overall, `{report['semantic_gap_adjudication']['confirmed_sealed_count']}` sealed.",
            f"- Phase 3B entry gate: `{gate['passed']}` (`{gate['decision']}`).",
            "- Direct production hybrid enablement: `False`.",
            "",
            "## Statistical Scope",
            "",
            "The Wilson interval in the artifact applies only to this deliberately adversarial synthetic fixture. It does not estimate the frequency of semantic misses in real user traffic and supports no population-level significance claim.",
            "",
        ]
    )
    return "\n".join(lines)


def render_analysis_markdown(report: dict[str, Any]) -> str:
    adjudication = report["semantic_gap_adjudication"]
    lines = [
        "# Memory Retrieval Semantic Gap Analysis",
        "",
        "> Every entry below is synthetic. Confirmed gaps satisfy the strict Phase 3A definition before being listed.",
        "",
        "## Strict Attribution",
        "",
        f"- Confirmed: `{adjudication['confirmed_count']}`.",
        f"- Analysis / sealed: `{adjudication['confirmed_analysis_count']}` / `{adjudication['confirmed_sealed_count']}`.",
        f"- Confirmed categories: `{adjudication['confirmed_category_count']}`.",
        f"- Non-semantic candidate misses: `{len(adjudication['non_semantic_candidate_misses'])}`.",
        f"- Downstream or successful non-gap cases: `{len(adjudication['downstream_or_success_cases'])}`.",
        "",
        "## Confirmed Gaps",
        "",
    ]
    for item in adjudication["confirmed"]:
        lines.extend(
            [
                f"### {item['case_id']}",
                "",
                f"- Category / primary: `{item['category']}` / `{item['primary_id']}`.",
                f"- Split / scope: `{item['split']}` / `{item['scope']}`.",
                f"- Language / overlap: `{item['language_direction']}` / `{item['lexical_overlap_bucket']}` (`{item['normalized_token_overlap']}`).",
                f"- First failure / rank: `{item['first_failure_stage']}` / `{item['diagnostic_rank']}`.",
                f"- Synthetic query: {item['synthetic_query_excerpt']}",
                f"- Semantic value: {item['why_semantic_gap']}",
                f"- Lexical failure mechanism: {item['why_lexical_may_fail']}",
                f"- Not metadata/scope/lifecycle/budget: {item['why_not_metadata_scope_lifecycle_budget']}",
                f"- Hard-negative controls: `{', '.join(item['hard_negative_controls'])}`.",
                f"- Future retrieval value: {item['future_value']}",
                "",
            ]
        )
    lines.extend(["## Non-Semantic Candidate Misses", ""])
    for item in adjudication["non_semantic_candidate_misses"]:
        lines.append(
            f"- `{item['case_id']}`: `{', '.join(item['exclusion_reasons'])}` "
            f"(stage `{item['first_failure_stage']}`, rank `{item['diagnostic_rank']}`)."
        )
    lines.extend(["", "## Downstream Or Successful Non-Gaps", ""])
    for item in adjudication["downstream_or_success_cases"]:
        lines.append(
            f"- `{item['case_id']}`: `{', '.join(item['exclusion_reasons'])}` "
            f"(stage `{item['first_failure_stage']}`, rank `{item['diagnostic_rank']}`)."
        )
    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            "This suite proves that the architecture can miss useful semantic relations under fixed lexical pressure. It does not prove how often those relations occur in production. File rename cases whose relevance depends primarily on explicit migration metadata remain structured-retrieval failures, not confirmed semantic-only gaps.",
            "",
        ]
    )
    return "\n".join(lines)


def render_performance_markdown(report: dict[str, Any]) -> str:
    performance = report["performance"]
    lines = [
        "# Memory Retrieval Semantic Gap Performance",
        "",
        "> Offline measurements on synthetic candidates; timings and memory are machine-dependent.",
        "",
        "| Candidates | Manager p50 / p95 ms | Canonical p50 / p95 ms | Evaluator projection p50 / p95 ms | Peak bytes | Selected | Cap |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for count, values in performance["scales"].items():
        lines.append(
            f"| {count} | {values['manager_search_p50_ms']:.3f} / {values['manager_search_p95_ms']:.3f} | "
            f"{values['canonical_retrieval_p50_ms']:.3f} / {values['canonical_retrieval_p95_ms']:.3f} | "
            f"{values['evaluator_projection_p50_ms']:.3f} / {values['evaluator_projection_p95_ms']:.3f} | "
            f"{values['peak_memory_bytes']} | {values['post_consolidation_count']} | "
            f"{values['candidate_consolidator_cap']} |"
        )
    lines.extend(
        [
            "",
            f"- Full evaluation peak traced memory: `{performance['evaluation_peak_memory_bytes']}` bytes.",
            f"- Per-case latency p50 / p95 / max: `{performance['case_latency_ms']['p50']:.3f}` / `{performance['case_latency_ms']['p95']:.3f}` / `{performance['case_latency_ms']['max']:.3f}` ms.",
            f"- Complexity: {performance['complexity_note']}",
            "- The production CandidateConsolidator limit remains 256; this phase does not change it.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(
    report: dict[str, Any],
    *,
    json_path: Path,
    baseline_markdown_path: Path,
    analysis_markdown_path: Path,
    performance_markdown_path: Path,
) -> None:
    outputs = {
        Path(json_path): json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        Path(baseline_markdown_path): render_baseline_markdown(report),
        Path(analysis_markdown_path): render_analysis_markdown(report),
        Path(performance_markdown_path): render_performance_markdown(report),
    }
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
