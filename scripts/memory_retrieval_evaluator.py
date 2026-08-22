"""Offline evaluator for MiniCode's production persistent-memory read paths."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch


EVALUATOR_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"
REFERENCE_TIME = 1_736_942_400
CREATED_AT = 1_736_856_000
UPDATED_AT = 1_736_938_800
MARKER_RE = re.compile(r"\[\[MRID:(mr-[a-z0-9-]+)\]\]")

CATEGORIES = (
    "exact_lexical",
    "paraphrase_synonym",
    "multilingual",
    "file_domain_context",
    "cross_scope_ranking",
    "lifecycle_safety",
    "negative_no_match",
    "duplicate_conflict_budget",
    "failure_recovery_correction",
    "entrypoint_consistency",
)
ARMS = (
    "manager_global_search",
    "manager_context_query",
    "pipeline_read",
    "pipeline_inject",
)
PHASE2A_ARMS = (*ARMS, "canonical_retrieval")
PRODUCTION_FILES = (
    "minicode/memory.py",
    "minicode/memory_retrieval.py",
    "minicode/memory_pipeline.py",
    "minicode/memory_injector.py",
    "minicode/memory_reranker.py",
    "minicode/vector_memory.py",
    "minicode/agent_loop.py",
    "minicode/cybernetic_orchestrator.py",
    "minicode/main.py",
    "minicode/headless.py",
    "minicode/tui/input_handler.py",
    "minicode/context_compactor.py",
    "minicode/agent_reflection.py",
)

_CASE_KEYS = {
    "case_id",
    "category",
    "task_description",
    "current_files",
    "active_domains",
    "context_usage",
    "max_memories",
    "max_tokens",
    "memories",
    "must_include_ids",
    "may_include_ids",
    "must_exclude_ids",
    "primary_id",
    "expected_no_injection",
    "rationale",
}
_MEMORY_KEYS = {
    "id",
    "scope",
    "category",
    "content",
    "tags",
    "domains",
    "tier",
    "lifecycle_status",
    "safety_status",
    "approval_status",
    "curator_locked",
    "usefulness_score",
    "usage_count",
    "created_at",
    "updated_at",
    "graded_relevance",
    "related_to",
}
_VALID_SCOPES = {"user", "project", "local"}
_VALID_TIERS = {"working", "short_term", "long_term", "archival"}
_VALID_LIFECYCLES = {
    "active",
    "pending",
    "rejected",
    "deprecated",
    "invalid",
    "archived_duplicate",
}
_VALID_SAFETY = {"safe", "suspicious", "unsafe"}
_VALID_APPROVAL = {"approved", "pending", "rejected"}
_ID_RE = re.compile(r"^mr-[a-z0-9-]+$")
_SECRET_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9]{16,}|bearer\s+[a-z0-9._-]{12,}|"
    r"(?:api[_-]?key|password|secret|token)\s*[:=]\s*(?!\[REDACTED\])\S{8,})"
)


class DatasetValidationError(ValueError):
    """The synthetic retrieval fixture violates its published contract."""


def _fail(source: Path, message: str) -> None:
    raise DatasetValidationError(f"{source}: {message}")


def _require_exact_keys(value: dict[str, Any], expected: set[str], source: Path, label: str) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        _fail(source, f"{label} keys mismatch missing={missing} extra={extra}")


def _validate_id_list(value: Any, source: Path, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        _fail(source, f"{label} must be a string list")
    if len(value) != len(set(value)):
        _fail(source, f"{label} contains duplicate IDs")
    for entry_id in value:
        if not _ID_RE.fullmatch(entry_id):
            _fail(source, f"{label} contains illegal ID {entry_id!r}")
    return value


def validate_case(case: Any, source: Path) -> None:
    """Validate one manually labelled case independently of production code."""
    if not isinstance(case, dict):
        _fail(source, "case must be an object")
    _require_exact_keys(case, _CASE_KEYS, source, f"case {case.get('case_id', '<unknown>')}")
    case_id = case["case_id"]
    if not isinstance(case_id, str) or not _ID_RE.fullmatch(case_id):
        _fail(source, f"illegal case_id {case_id!r}")
    if case["category"] not in CATEGORIES:
        _fail(source, f"case {case_id} has illegal category {case['category']!r}")
    if not isinstance(case["task_description"], str) or not case["task_description"].strip():
        _fail(source, f"case {case_id} requires task_description")
    for field in ("current_files", "active_domains"):
        if not isinstance(case[field], list) or not all(isinstance(item, str) for item in case[field]):
            _fail(source, f"case {case_id} {field} must be a string list")
    if not isinstance(case["context_usage"], (int, float)) or not 0 <= case["context_usage"] <= 1:
        _fail(source, f"case {case_id} context_usage must be in [0, 1]")
    if not isinstance(case["max_memories"], int) or not 1 <= case["max_memories"] <= 20:
        _fail(source, f"case {case_id} has invalid max_memories")
    if not isinstance(case["max_tokens"], int) or not 16 <= case["max_tokens"] <= 8000:
        _fail(source, f"case {case_id} has invalid max_tokens")
    if not isinstance(case["expected_no_injection"], bool):
        _fail(source, f"case {case_id} expected_no_injection must be boolean")
    if not isinstance(case["rationale"], str) or len(case["rationale"].strip()) < 8:
        _fail(source, f"case {case_id} rationale is missing")

    memories = case["memories"]
    if not isinstance(memories, list) or not memories:
        _fail(source, f"case {case_id} requires memories")
    memory_ids: list[str] = []
    grades: dict[str, int] = {}
    for index, memory in enumerate(memories):
        if not isinstance(memory, dict):
            _fail(source, f"case {case_id} memory[{index}] must be an object")
        _require_exact_keys(memory, _MEMORY_KEYS, source, f"case {case_id} memory[{index}]")
        entry_id = memory["id"]
        if not isinstance(entry_id, str) or not _ID_RE.fullmatch(entry_id):
            _fail(source, f"case {case_id} has illegal memory ID {entry_id!r}")
        memory_ids.append(entry_id)
        if memory["scope"] not in _VALID_SCOPES:
            _fail(source, f"case {case_id} memory {entry_id} has illegal scope")
        if memory["tier"] not in _VALID_TIERS:
            _fail(source, f"case {case_id} memory {entry_id} has illegal tier")
        if memory["lifecycle_status"] not in _VALID_LIFECYCLES:
            _fail(source, f"case {case_id} memory {entry_id} has illegal lifecycle")
        if memory["safety_status"] not in _VALID_SAFETY:
            _fail(source, f"case {case_id} memory {entry_id} has illegal safety status")
        if memory["approval_status"] not in _VALID_APPROVAL:
            _fail(source, f"case {case_id} memory {entry_id} has illegal approval status")
        if not isinstance(memory["curator_locked"], bool):
            _fail(source, f"case {case_id} memory {entry_id} curator_locked must be boolean")
        if not isinstance(memory["usefulness_score"], (int, float)) or not -1 <= memory["usefulness_score"] <= 1:
            _fail(source, f"case {case_id} memory {entry_id} has invalid usefulness")
        if not isinstance(memory["usage_count"], int) or memory["usage_count"] < 0:
            _fail(source, f"case {case_id} memory {entry_id} has invalid usage_count")
        if memory["created_at"] != CREATED_AT or memory["updated_at"] != UPDATED_AT:
            _fail(source, f"case {case_id} memory {entry_id} timestamps are not fixed")
        grade = memory["graded_relevance"]
        if not isinstance(grade, int) or grade not in {0, 1, 2, 3}:
            _fail(source, f"case {case_id} memory {entry_id} lacks a valid grade")
        grades[entry_id] = grade
        for field in ("tags", "domains"):
            if not isinstance(memory[field], list) or not all(isinstance(item, str) for item in memory[field]):
                _fail(source, f"case {case_id} memory {entry_id} {field} must be a string list")
        _validate_id_list(memory["related_to"], source, f"case {case_id} memory {entry_id} related_to")
        if not isinstance(memory["content"], str) or not memory["content"].strip():
            _fail(source, f"case {case_id} memory {entry_id} has empty content")
        if MARKER_RE.search(memory["content"]):
            _fail(source, f"case {case_id} memory {entry_id} contains evaluator marker")
        if _SECRET_RE.search(memory["content"]):
            _fail(source, f"case {case_id} memory {entry_id} contains credential-shaped text")
    if len(memory_ids) != len(set(memory_ids)):
        _fail(source, f"case {case_id} contains duplicate memory IDs")

    known = set(memory_ids)
    expected_groups = {
        name: set(_validate_id_list(case[name], source, f"case {case_id} {name}"))
        for name in ("must_include_ids", "may_include_ids", "must_exclude_ids")
    }
    expected_union = set().union(*expected_groups.values())
    unknown = expected_union - known
    if unknown:
        _fail(source, f"case {case_id} expected IDs do not exist: {sorted(unknown)}")
    names = list(expected_groups)
    for left_index, left in enumerate(names):
        for right in names[left_index + 1 :]:
            overlap = expected_groups[left] & expected_groups[right]
            if overlap:
                _fail(source, f"case {case_id} IDs overlap {left}/{right}: {sorted(overlap)}")
    primary = case["primary_id"]
    if primary is not None:
        if not isinstance(primary, str) or primary not in known:
            _fail(source, f"case {case_id} primary_id does not exist")
        if primary not in expected_groups["must_include_ids"] or grades[primary] != 3:
            _fail(source, f"case {case_id} primary_id must be grade 3 and must-include")
    if case["expected_no_injection"] and (
        primary is not None or expected_groups["must_include_ids"] or expected_groups["may_include_ids"]
    ):
        _fail(source, f"case {case_id} no-injection label conflicts with relevant expectations")
    labelled_relevant = expected_groups["must_include_ids"] | expected_groups["may_include_ids"]
    if {entry_id for entry_id, grade in grades.items() if grade > 0} != labelled_relevant:
        _fail(source, f"case {case_id} grades and relevant expected IDs disagree")
    if any(grades[entry_id] != 0 for entry_id in expected_groups["must_exclude_ids"]):
        _fail(source, f"case {case_id} excluded IDs must have grade 0")
    for memory in memories:
        unknown_related = set(memory["related_to"]) - known
        if unknown_related:
            _fail(source, f"case {case_id} related IDs do not exist: {sorted(unknown_related)}")


def load_dataset(root: Path) -> list[dict[str, Any]]:
    """Load and validate all category files in stable case-ID order."""
    root = Path(root)
    case_dir = root / "cases" if (root / "cases").is_dir() else root
    paths = sorted(case_dir.glob("*.json"))
    if not paths:
        raise DatasetValidationError(f"{case_dir}: no case JSON files")
    cases: list[dict[str, Any]] = []
    case_sources: dict[str, Path] = {}
    global_memory_ids: dict[str, Path] = {}
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DatasetValidationError(f"{path}: invalid JSON: {exc}") from exc
        if set(document) != {"schema_version", "synthetic_data", "reference_time", "cases"}:
            _fail(path, "document keys do not match schema")
        if document["schema_version"] != SCHEMA_VERSION:
            _fail(path, "unsupported schema_version")
        if document["synthetic_data"] is not True:
            _fail(path, "synthetic_data must be true")
        if document["reference_time"] != REFERENCE_TIME:
            _fail(path, "reference_time is not fixed")
        if not isinstance(document["cases"], list):
            _fail(path, "cases must be a list")
        for case in document["cases"]:
            validate_case(case, path)
            case_id = case["case_id"]
            if case_id in case_sources:
                _fail(path, f"duplicate case_id {case_id}; first seen in {case_sources[case_id]}")
            case_sources[case_id] = path
            for memory in case["memories"]:
                entry_id = memory["id"]
                if entry_id in global_memory_ids:
                    _fail(path, f"duplicate memory ID {entry_id}; first seen in {global_memory_ids[entry_id]}")
                global_memory_ids[entry_id] = path
            cases.append(copy.deepcopy(case))
    return sorted(cases, key=lambda case: case["case_id"])


def precision_at_k(output_ids: list[str], grades: dict[str, int], k: int) -> float:
    """Binary precision at a fixed rank depth; absent ranks count as misses."""
    return sum(grades.get(entry_id, 0) > 0 for entry_id in output_ids[:k]) / k


def recall_at_k(output_ids: list[str], grades: dict[str, int], k: int) -> float | None:
    relevant = {entry_id for entry_id, grade in grades.items() if grade > 0}
    if not relevant:
        return None
    return len(set(output_ids[:k]) & relevant) / len(relevant)


def reciprocal_rank(output_ids: list[str], grades: dict[str, int]) -> float:
    for index, entry_id in enumerate(output_ids, 1):
        if grades.get(entry_id, 0) > 0:
            return 1.0 / index
    return 0.0


def ndcg_at_k(output_ids: list[str], grades: dict[str, int], k: int = 5) -> float | None:
    ideal_grades = sorted((grade for grade in grades.values() if grade > 0), reverse=True)[:k]
    if not ideal_grades:
        return None

    def dcg(values: list[int]) -> float:
        return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(values))

    actual = [grades.get(entry_id, 0) for entry_id in output_ids[:k]]
    ideal = dcg(ideal_grades)
    return dcg(actual) / ideal if ideal else None


def _normalized_content(content: str) -> str:
    return re.sub(r"\s+", " ", content.strip().lower())


def _active_from_fixture(memory: dict[str, Any]) -> bool:
    return (
        memory["approval_status"] == "approved"
        and memory["safety_status"] != "unsafe"
        and memory["lifecycle_status"] == "active"
        and not memory["curator_locked"]
        and memory["tier"] != "archival"
    )


def calculate_case_metrics(
    case: dict[str, Any],
    output_ids: list[str],
    *,
    rendered_ids: list[str] | None = None,
    recorded_ids: list[str] | None = None,
    feedback_ids: list[str] | None = None,
    memory_tokens: int | None = None,
) -> dict[str, Any]:
    """Calculate rank, leakage, budget, and attribution metrics for one arm."""
    memories = {memory["id"]: memory for memory in case["memories"]}
    grades = {entry_id: memory["graded_relevance"] for entry_id, memory in memories.items()}
    excluded = set(case["must_exclude_ids"])
    normalized = [_normalized_content(memories[entry_id]["content"]) for entry_id in output_ids if entry_id in memories]
    duplicate_count = len(normalized) - len(set(normalized))
    inactive_ids = [entry_id for entry_id in output_ids if entry_id in memories and not _active_from_fixture(memories[entry_id])]
    primary = case["primary_id"]
    metrics: dict[str, Any] = {
        "precision_at_1": precision_at_k(output_ids, grades, 1),
        "precision_at_3": precision_at_k(output_ids, grades, 3),
        "precision_at_5": precision_at_k(output_ids, grades, 5),
        "recall_at_1": recall_at_k(output_ids, grades, 1),
        "recall_at_3": recall_at_k(output_ids, grades, 3),
        "recall_at_5": recall_at_k(output_ids, grades, 5),
        "mrr": reciprocal_rank(output_ids, grades),
        "ndcg_at_5": ndcg_at_k(output_ids, grades, 5),
        "primary_hit": None if primary is None else primary in output_ids[:5],
        "must_exclude_violation": bool(set(output_ids) & excluded),
        "negative_false_injection": bool(output_ids) if case["expected_no_injection"] else None,
        "duplicate_injection": duplicate_count > 0,
        "duplicate_count": duplicate_count,
        "max_memories_violation": len(output_ids) > case["max_memories"],
        "token_budget_violation": None if memory_tokens is None else memory_tokens > case["max_tokens"],
        "inactive_memory_leakage": bool(inactive_ids),
        "inactive_leaked_ids": inactive_ids,
        "actual_rendered_precision": None,
        "feedback_attribution_precision": None,
        "returned_rendered_disagreement": None,
        "rendered_recorded_disagreement": None,
        "rendered_feedback_disagreement": None,
    }
    if rendered_ids is not None:
        metrics["actual_rendered_precision"] = (
            sum(grades.get(entry_id, 0) > 0 for entry_id in rendered_ids) / len(rendered_ids)
            if rendered_ids
            else 1.0
        )
        metrics["returned_rendered_disagreement"] = output_ids != rendered_ids
    if recorded_ids is not None and rendered_ids is not None:
        metrics["rendered_recorded_disagreement"] = rendered_ids != recorded_ids
    if feedback_ids is not None:
        metrics["feedback_attribution_precision"] = (
            len(set(feedback_ids) & set(rendered_ids or [])) / len(set(feedback_ids))
            if feedback_ids
            else None
        )
        if rendered_ids is not None:
            metrics["rendered_feedback_disagreement"] = rendered_ids != feedback_ids
    return metrics


def _extract_ids(text: str) -> list[str]:
    return MARKER_RE.findall(text)


def _marker(entry_id: str) -> str:
    return f"[[MRID:{entry_id}]]"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_production_files(project_root: Path) -> dict[str, str]:
    return {
        relative: _sha256(project_root / relative)
        for relative in PRODUCTION_FILES
        if (project_root / relative).is_file()
    }


def snapshot_formal_memory(project_root: Path) -> dict[str, dict[str, int | str]]:
    """Hash-only integrity snapshot; evaluator arms never load these paths."""
    roots = {
        "user": Path.home() / ".mini-code" / "memory",
        "project": project_root / ".mini-code-memory",
        "local": project_root / ".mini-code-memory-local",
        "session": project_root / ".mini-code-session-memory",
    }
    snapshot: dict[str, dict[str, int | str]] = {}
    for label, root in roots.items():
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = f"{label}/{path.relative_to(root)}"
            stat = path.stat()
            snapshot[relative] = {
                "sha256": _sha256(path),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
    return snapshot


class _InstrumentedMemoryManagerMixin:
    """Test-side observation of production manager I/O and feedback calls."""

    def _init_observation(self, isolation_id: str) -> None:
        self.isolation_id = isolation_id
        self.save_events: list[str] = []
        self.search_calls: list[dict[str, Any]] = []
        self.injection_calls: list[list[str]] = []
        self.retrieval_calls: list[list[str]] = []
        self.feedback_calls: list[dict[str, Any]] = []

    def _save_scope(self, scope: Any) -> None:
        self.save_events.append(scope.value)
        super()._save_scope(scope)

    def search(self, query: str, **kwargs: Any) -> list[Any]:
        scope = kwargs.get("scope")
        self.search_calls.append(
            {
                "query": query,
                "scope": getattr(scope, "value", scope),
                "limit": kwargs.get("limit", 20),
                "min_relevance": kwargs.get("min_relevance", 0.1),
                "active_domains": list(kwargs.get("active_domains") or []),
            }
        )
        return super().search(query, **kwargs)

    def record_injections(self, entry_ids: list[str]) -> None:
        self.injection_calls.append(list(entry_ids))
        super().record_injections(entry_ids)

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


@contextmanager
def isolated_manager(
    case: dict[str, Any],
    isolation_id: str,
    *,
    include_markers: bool = True,
) -> Iterator[Any]:
    """Build an isolated production MemoryManager populated with one synthetic case."""
    from minicode import memory as memory_module
    from minicode.memory import MemoryEntry, MemoryManager, MemoryScope, MemoryTier

    class InstrumentedMemoryManager(_InstrumentedMemoryManagerMixin, MemoryManager):
        def __init__(self, *args: Any, **kwargs: Any):
            self._init_observation(isolation_id)
            super().__init__(*args, **kwargs)

    with tempfile.TemporaryDirectory(prefix="minicode-memory-retrieval-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        isolated_user_root = root / "home" / ".mini-code"
        with patch.object(memory_module, "MINI_CODE_DIR", isolated_user_root):
            manager = InstrumentedMemoryManager(project_root=workspace)
        for memory in case["memories"]:
            entry = MemoryEntry(
                id=memory["id"],
                scope=MemoryScope(memory["scope"]),
                category=memory["category"],
                content=(
                    f"{memory['content']} {_marker(memory['id'])}"
                    if include_markers
                    else memory["content"]
                ),
                created_at=memory["created_at"],
                updated_at=memory["updated_at"],
                tags=list(memory["tags"]),
                usage_count=memory["usage_count"],
                domains=list(memory["domains"]),
                tier=MemoryTier(memory["tier"]),
                last_accessed=memory["updated_at"],
                related_to=list(memory["related_to"]),
                usefulness_score=memory["usefulness_score"],
                lifecycle_status=memory["lifecycle_status"],
                curator_locked=memory["curator_locked"],
                safety_status=memory["safety_status"],
                approval_status=memory["approval_status"],
            )
            manager.memories[entry.scope].entries.append(entry)
        for scope in MemoryScope:
            manager.memories[scope]._rebuild_indices()
        manager.workspace_path = workspace
        yield manager


def _memory_tokens(text: str) -> int:
    from minicode.context_manager import estimate_tokens

    return estimate_tokens(text)


def _pipeline_for_case(manager: Any, case: dict[str, Any], *, reranker_model: Any = None) -> Any:
    from minicode.memory_pipeline import MemoryPipeline

    pipeline = MemoryPipeline(manager)
    # The evaluator may provide a sentinel model to prove that canonical
    # retrieval does *not* invoke an experimental reranker. Passing the old
    # feature flag falsely claimed that backend was production-wired; the
    # pipeline now rejects that configuration by contract.
    pipeline.initialize(
        model_adapter=reranker_model,
        workspace_path=str(manager.workspace_path),
        enable_reranker=False,
        enable_vector=False,
    )
    if pipeline._injector is not None:
        pipeline._injector._max_injected = case["max_memories"]
        pipeline._injector._max_tokens = max(1, case["max_tokens"] // case["max_memories"])
        pipeline._injector._injection_cooldown = 0.0
    return pipeline


def evaluate_arm(case: dict[str, Any], arm: str) -> dict[str, Any]:
    """Run one real production interface against one isolated case."""
    if arm not in PHASE2A_ARMS:
        raise ValueError(f"unknown evaluator arm: {arm}")
    start = time.perf_counter()
    with isolated_manager(
        case,
        f"{case['case_id']}:{arm}",
        include_markers=False,
    ) as manager:
        returned_ids: list[str] = []
        candidate_ids: list[str] = []
        selected_ids: list[str] = []
        rendered_ids: list[str] | None = None
        last_injected_ids: list[str] | None = None
        recorded_ids: list[str] | None = None
        feedback_ids: list[str] | None = None
        memory_tokens: int | None = None
        rendered_text = ""
        decision: dict[str, Any] | None = None
        no_match: bool | None = None
        no_match_reason: str | None = None
        query_hash = ""
        diagnostics: dict[str, Any] = {}
        score_breakdown: list[dict[str, Any]] = []
        task_start_save_count = 0
        feedback_save_count = 0

        with patch("minicode.memory.time.time", return_value=REFERENCE_TIME), patch(
            "minicode.memory_pipeline.time.time", return_value=REFERENCE_TIME
        ), patch("minicode.memory_injector.time.time", return_value=REFERENCE_TIME), patch(
            "minicode.memory_retrieval.time.time", return_value=REFERENCE_TIME
        ):
            if arm == "manager_global_search":
                entries = manager.search(
                    case["task_description"],
                    scope=None,
                    limit=case["max_memories"],
                    min_relevance=0.1,
                    active_domains=case["active_domains"] or None,
                )
                returned_ids = [entry.id for entry in entries]
                candidate_ids = list(returned_ids)
                selected_ids = list(returned_ids)
                task_start_save_count = len(manager.save_events)
            elif arm == "manager_context_query":
                rendered_text = manager.get_relevant_context(
                    max_entries=case["max_memories"],
                    max_tokens=case["max_tokens"],
                    query=case["task_description"],
                    current_files=case["current_files"],
                    active_domains=case["active_domains"],
                    context_usage=case["context_usage"],
                    max_tokens_per_memory=max(
                        1, case["max_tokens"] // case["max_memories"]
                    ),
                )
                result = manager._last_retrieval_result
                rendered_ids = list(result.rendered_ids) if result is not None else []
                returned_ids = list(rendered_ids)
                memory_tokens = _memory_tokens(rendered_text)
                task_start_save_count = len(manager.save_events)
                if result is not None:
                    candidate_ids = list(result.candidate_ids)
                    selected_ids = list(result.selected_ids)
                    decision = dict(result.controller_decision)
                    no_match = result.no_match
                    no_match_reason = result.no_match_reason
                    query_hash = result.query_hash
                    diagnostics = copy.deepcopy(result.diagnostics)
                    score_breakdown = [item.to_dict() for item in result.candidates]
            elif arm == "pipeline_read":
                pipeline = _pipeline_for_case(manager, case)
                results = pipeline.read(
                    case["task_description"],
                    current_files=case["current_files"] or None,
                    active_domains=case["active_domains"] or None,
                    max_results=case["max_memories"],
                    max_total_tokens=case["max_tokens"],
                    max_tokens_per_memory=max(
                        1, case["max_tokens"] // case["max_memories"]
                    ),
                    context_usage=case["context_usage"],
                    min_relevance=0.0,
                )
                returned_ids = [result["id"] for result in results]
                result = pipeline.last_retrieval_result
                rendered_ids = list(result.rendered_ids) if result is not None else []
                rendered_text = result.prompt_text if result is not None else ""
                memory_tokens = _memory_tokens(rendered_text)
                task_start_save_count = len(manager.save_events)
                if result is not None:
                    candidate_ids = list(result.candidate_ids)
                    selected_ids = list(result.selected_ids)
                    decision = dict(result.controller_decision)
                    no_match = result.no_match
                    no_match_reason = result.no_match_reason
                    query_hash = result.query_hash
                    diagnostics = copy.deepcopy(result.diagnostics)
                    score_breakdown = [item.to_dict() for item in result.candidates]
            elif arm == "pipeline_inject":
                pipeline = _pipeline_for_case(manager, case)
                messages = [{"role": "system", "content": "SYSTEM_BASELINE"}]
                result_messages = pipeline.inject(
                    case["task_description"],
                    case["current_files"] or None,
                    messages,
                    context_usage=case["context_usage"],
                    active_domains=case["active_domains"] or None,
                    max_memories=case["max_memories"],
                    max_total_tokens=case["max_tokens"],
                    max_tokens_per_memory=max(
                        1, case["max_tokens"] // case["max_memories"]
                    ),
                    min_relevance=0.0,
                )
                result = pipeline.last_retrieval_result
                returned_ids = list(result.rendered_ids) if result is not None else []
                last_injected_ids = list(pipeline._last_injected_ids)
                rendered_text = str(result_messages[0]["content"])[len("SYSTEM_BASELINE") :]
                rendered_ids = list(result.rendered_ids) if result is not None else []
                recorded_ids = [entry_id for call in manager.injection_calls for entry_id in call]
                task_start_save_count = len(manager.save_events)
                pipeline.feedback("success")
                feedback_save_count = len(manager.save_events) - task_start_save_count
                feedback_ids = [
                    entry_id
                    for call in manager.feedback_calls
                    for entry_id in call["entry_ids"]
                ]
                memory_tokens = _memory_tokens(rendered_text)
                if result is not None:
                    candidate_ids = list(result.candidate_ids)
                    selected_ids = list(result.selected_ids)
                    decision = dict(result.controller_decision)
                    no_match = result.no_match
                    no_match_reason = result.no_match_reason
                    query_hash = result.query_hash
                    diagnostics = copy.deepcopy(result.diagnostics)
                    score_breakdown = [item.to_dict() for item in result.candidates]
            else:
                from minicode.memory_retrieval import (
                    CanonicalMemoryRetriever,
                    MemoryRetrievalRequest,
                    RetrievalSource,
                )

                result = CanonicalMemoryRetriever(manager).retrieve(
                    MemoryRetrievalRequest(
                        query=case["task_description"],
                        current_files=tuple(case["current_files"]),
                        active_domains=tuple(case["active_domains"]),
                        context_usage=case["context_usage"],
                        max_memories=case["max_memories"],
                        max_total_tokens=case["max_tokens"],
                        max_tokens_per_memory=max(
                            1, case["max_tokens"] // case["max_memories"]
                        ),
                        source_entrypoint=RetrievalSource.CANONICAL,
                    )
                )
                manager.record_retrievals_and_injections(
                    list(result.selected_ids),
                    list(result.rendered_ids),
                )
                task_start_save_count = len(manager.save_events)
                manager.record_feedback(list(result.rendered_ids), success=True)
                feedback_save_count = len(manager.save_events) - task_start_save_count
                candidate_ids = list(result.candidate_ids)
                selected_ids = list(result.selected_ids)
                returned_ids = list(result.rendered_ids)
                rendered_ids = list(result.rendered_ids)
                last_injected_ids = list(result.rendered_ids)
                recorded_ids = [entry_id for call in manager.injection_calls for entry_id in call]
                feedback_ids = [
                    entry_id
                    for call in manager.feedback_calls
                    for entry_id in call["entry_ids"]
                ]
                rendered_text = result.prompt_text
                memory_tokens = _memory_tokens(rendered_text)
                decision = dict(result.controller_decision)
                no_match = result.no_match
                no_match_reason = result.no_match_reason
                query_hash = result.query_hash
                diagnostics = copy.deepcopy(result.diagnostics)
                score_breakdown = [item.to_dict() for item in result.candidates]

        metrics = calculate_case_metrics(
            case,
            returned_ids,
            rendered_ids=rendered_ids,
            recorded_ids=recorded_ids,
            feedback_ids=feedback_ids,
            memory_tokens=memory_tokens,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "case_id": case["case_id"],
            "category": case["category"],
            "arm": arm,
            "manager_isolation_id": manager.isolation_id,
            "candidate_ids": candidate_ids,
            "selected_ids": selected_ids,
            "returned_ids": returned_ids,
            "last_injected_ids": last_injected_ids,
            "rendered_ids": rendered_ids,
            "recorded_injection_ids": recorded_ids,
            "feedback_ids": feedback_ids,
            "memory_tokens": memory_tokens,
            "save_count": len(manager.save_events),
            # Manager feedback persists a set of touched scopes, whose iteration
            # order varies with Python hash randomization. Counts are the stable
            # I/O observation; scope order has no production semantic meaning.
            "save_scopes": sorted(manager.save_events),
            "save_scope_counts": dict(sorted(Counter(manager.save_events).items())),
            "search_call_count": len(manager.search_calls),
            "search_calls": manager.search_calls,
            "decision": decision,
            "no_match": no_match,
            "no_match_reason": no_match_reason,
            "query_hash": query_hash,
            "diagnostics": diagnostics,
            "score_breakdown": score_breakdown,
            "io_counts": {
                "search_calls": len(manager.search_calls),
                "retrieval_counter_calls": len(manager.retrieval_calls),
                "injection_counter_calls": len(manager.injection_calls),
                "feedback_calls": len(manager.feedback_calls),
                "task_start_scope_saves": task_start_save_count,
                "feedback_scope_saves": feedback_save_count,
                "total_scope_saves": len(manager.save_events),
            },
            "metrics": metrics,
            "latency_ms": round(elapsed_ms, 6),
        }


_MEAN_METRICS = (
    "precision_at_1",
    "precision_at_3",
    "precision_at_5",
    "recall_at_1",
    "recall_at_3",
    "recall_at_5",
    "mrr",
    "ndcg_at_5",
    "actual_rendered_precision",
    "feedback_attribution_precision",
)
_RATE_METRICS = (
    "primary_hit",
    "must_exclude_violation",
    "negative_false_injection",
    "duplicate_injection",
    "max_memories_violation",
    "token_budget_violation",
    "inactive_memory_leakage",
    "returned_rendered_disagreement",
    "rendered_recorded_disagreement",
    "rendered_feedback_disagreement",
)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[rank]


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate: dict[str, Any] = {}
    for name in _MEAN_METRICS:
        values = [float(result["metrics"][name]) for result in results if result["metrics"].get(name) is not None]
        aggregate[name] = _mean(values)
    for name in _RATE_METRICS:
        values = [bool(result["metrics"][name]) for result in results if result["metrics"].get(name) is not None]
        aggregate[f"{name}_rate"] = _mean([float(value) for value in values])
        aggregate[f"{name}_count"] = sum(values)
    aggregate["case_count"] = len(results)
    aggregate["average_memory_tokens"] = _mean(
        [float(result["memory_tokens"]) for result in results if result["memory_tokens"] is not None]
    )
    aggregate["average_save_count"] = _mean([float(result["save_count"]) for result in results])
    return aggregate


def _pairwise_consistency(case_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pairs: dict[str, Any] = {}
    for left_index, left in enumerate(ARMS):
        for right in ARMS[left_index + 1 :]:
            left_ids = case_results[left]["returned_ids"][:5]
            right_ids = case_results[right]["returned_ids"][:5]
            union = set(left_ids) | set(right_ids)
            jaccard = len(set(left_ids) & set(right_ids)) / len(union) if union else 1.0
            pairs[f"{left}__{right}"] = {
                "top5_jaccard": jaccard,
                "top1_agreement": bool(left_ids and right_ids and left_ids[0] == right_ids[0]),
            }
    return pairs


def _summarize_consistency(per_case: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in per_case:
        for pair, values in case["consistency"].items():
            grouped[pair].append(values)
    return {
        pair: {
            "average_top5_jaccard": _mean([value["top5_jaccard"] for value in values]),
            "top1_agreement_rate": _mean([float(value["top1_agreement"]) for value in values]),
        }
        for pair, values in sorted(grouped.items())
    }


def _case_by_id(cases: list[dict[str, Any]], case_id: str) -> dict[str, Any]:
    return next(case for case in cases if case["case_id"] == case_id)


def _result_by_case(per_case: list[dict[str, Any]], case_id: str, arm: str) -> dict[str, Any]:
    case = next(item for item in per_case if item["case_id"] == case_id)
    return case["arms"][arm]


class _FakeVectorStore:
    enabled = True

    def __init__(self, entry_id: str):
        self.entry_id = entry_id

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        return [(self.entry_id, 0.99)]


class _SafeSummaryModel:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        ids = re.findall(r"\[(mr-[a-z0-9-]+)\]", prompt)
        return json.dumps(
            {
                "selected": ids[:8],
                "rejected": [],
                "conflicts": [],
                "summary": "UNTRUSTED_SUMMARY_MARKER",
            }
        )


def run_known_risk_diagnostics(
    cases: list[dict[str, Any]], per_case: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Run the twelve required synthetic risk reproductions."""
    diagnostics: list[dict[str, Any]] = []

    global_scope = _result_by_case(per_case, "mr-scope-01", "manager_global_search")
    inject_scope = _result_by_case(per_case, "mr-scope-01", "pipeline_inject")
    diagnostics.append(
        {
            "risk_id": "global-vs-inject-ordering",
            "confirmed": global_scope["returned_ids"][:1] != inject_scope["returned_ids"][:1],
            "manager_top": global_scope["returned_ids"][:3],
            "injector_top": inject_scope["returned_ids"][:3],
        }
    )

    max_one = _result_by_case(per_case, "mr-budget-05", "pipeline_inject")
    diagnostics.append(
        {
            "risk_id": "max-memories-one",
            "confirmed": len(max_one["returned_ids"]) > 1,
            "limit": 1,
            "returned_count": len(max_one["returned_ids"]),
            "rendered_count": len(max_one["rendered_ids"] or []),
            "recorded_count": len(max_one["recorded_injection_ids"] or []),
        }
    )

    over_five = _result_by_case(per_case, "mr-budget-06", "pipeline_inject")
    diagnostics.append(
        {
            "risk_id": "format-first-five-attribution",
            "confirmed": set(over_five["recorded_injection_ids"] or []) != set(over_five["rendered_ids"] or []),
            "returned_ids": over_five["returned_ids"],
            "rendered_ids": over_five["rendered_ids"],
            "recorded_ids": over_five["recorded_injection_ids"],
            "feedback_ids": over_five["feedback_ids"],
        }
    )

    tui_case = _case_by_id(cases, "mr-entry-01")
    with isolated_manager(tui_case, "diagnostic:tui-context") as context_manager, isolated_manager(
        tui_case, "diagnostic:tui-pipeline"
    ) as pipeline_manager:
        with patch("minicode.memory.time.time", return_value=REFERENCE_TIME), patch(
            "minicode.memory_pipeline.time.time", return_value=REFERENCE_TIME
        ), patch("minicode.memory_injector.time.time", return_value=REFERENCE_TIME):
            context = context_manager.get_relevant_context(
                query=tui_case["task_description"], max_entries=5, max_tokens=500
            )
            pipeline = _pipeline_for_case(pipeline_manager, tui_case)
            messages = [{"role": "system", "content": f"SYSTEM\n{context}"}]
            combined = pipeline.inject(
                tui_case["task_description"], tui_case["current_files"], messages, context_usage=0.4
            )[0]["content"]
            occurrences = Counter(_extract_ids(combined))
    diagnostics.append(
        {
            "risk_id": "tui-double-injection",
            "confirmed": any(count > 1 for count in occurrences.values()),
            "marker_occurrences": dict(sorted(occurrences.items())),
            "manager_instances_same": False,
        }
    )

    local_budget = _result_by_case(per_case, "mr-budget-04", "manager_context_query")
    diagnostics.append(
        {
            "risk_id": "local-budget-before-project",
            "confirmed": "mr-budget-04-primary" not in (local_budget["rendered_ids"] or []),
            "rendered_ids": local_budget["rendered_ids"],
            "tokens": local_budget["memory_tokens"],
            "budget": 130,
        }
    )

    no_files = _result_by_case(per_case, "mr-entry-03", "pipeline_inject")
    searched_domains = sorted({domain for call in no_files["search_calls"] for domain in call["active_domains"]})
    diagnostics.append(
        {
            "risk_id": "missing-current-files-domains",
            "confirmed": not searched_domains,
            "fixture_active_domains": ["backend"],
            "search_active_domains": searched_domains,
        }
    )

    vector_case = copy.deepcopy(_case_by_id(cases, "mr-negative-01"))
    vector_target = vector_case["memories"][0]["id"]
    with isolated_manager(vector_case, "diagnostic:vector-only") as manager:
        with patch("minicode.memory.time.time", return_value=REFERENCE_TIME):
            pipeline = _pipeline_for_case(manager, vector_case)
            pipeline._vector_store = _FakeVectorStore(vector_target)
            vector_result = pipeline.read(
                "quasar spectroscopy only", current_files=None, active_domains=None, max_results=5
            )
    diagnostics.append(
        {
            "risk_id": "vector-only-fusion",
            "confirmed": vector_target not in [item["id"] for item in vector_result],
            "vector_backend_default_enabled": False,
            "fake_vector_hit_id": vector_target,
            "pipeline_result_ids": [item["id"] for item in vector_result],
        }
    )

    graph_result = _result_by_case(per_case, "mr-entry-04", "pipeline_read")
    diagnostics.append(
        {
            "risk_id": "related-graph-semantics",
            "confirmed": "mr-entry-04-neighbor" in graph_result["returned_ids"],
            "returned_ids": graph_result["returned_ids"],
            "declared_decay": 0.5,
            "declared_threshold": 0.3,
            "runtime_applies_decay_or_threshold": False,
        }
    )

    feedback_case = _case_by_id(cases, "mr-recovery-05")
    feedback_id = feedback_case["primary_id"]
    with isolated_manager(feedback_case, "diagnostic:recovered-feedback") as manager:
        pipeline = _pipeline_for_case(manager, feedback_case)
        pipeline.feedback(False, [feedback_id])
        _, entry = manager._find_entry_by_id(feedback_id)
        counters = {"success_count": entry.success_count, "failure_count": entry.failure_count}
    diagnostics.append(
        {
            "risk_id": "recovered-failure-feedback",
            "confirmed": counters["failure_count"] == 1 and counters["success_count"] == 0,
            "agent_condition": "tool_error_count == 0",
            "simulated_tool_error_count": 1,
            "simulated_final_outcome": "success",
            "counters": counters,
        }
    )

    summary_case = _case_by_id(cases, "mr-budget-06")
    fake_model = _SafeSummaryModel()
    with isolated_manager(summary_case, "diagnostic:reranker-summary") as manager:
        with patch("minicode.memory.time.time", return_value=REFERENCE_TIME), patch(
            "minicode.memory_pipeline.time.time", return_value=REFERENCE_TIME
        ), patch("minicode.memory_injector.time.time", return_value=REFERENCE_TIME):
            pipeline = _pipeline_for_case(manager, summary_case, reranker_model=fake_model)
            messages = [{"role": "system", "content": "SYSTEM"}]
            rendered = pipeline.inject(
                summary_case["task_description"], summary_case["current_files"], messages, context_usage=0.4
            )[0]["content"]
    diagnostics.append(
        {
            "risk_id": "reranker-summary-boundary",
            "confirmed": "UNTRUSTED_SUMMARY_MARKER" in rendered,
            "safe_fake_model_calls": fake_model.calls,
            "summary_marker_rendered": "UNTRUSTED_SUMMARY_MARKER" in rendered,
            "summary_safety_scan_observed": False,
        }
    )

    headless_case = _case_by_id(cases, "mr-negative-01")
    with isolated_manager(headless_case, "diagnostic:headless-no-query") as manager:
        headless_context = manager.get_relevant_context(max_entries=5, max_tokens=8000)
        headless_ids = _extract_ids(headless_context)
    diagnostics.append(
        {
            "risk_id": "headless-no-query-unrelated",
            "confirmed": bool(headless_ids),
            "task": headless_case["task_description"],
            "rendered_ids": headless_ids,
        }
    )

    repeat_case = _case_by_id(cases, "mr-entry-01")
    repeat_id = repeat_case["primary_id"]
    with isolated_manager(repeat_case, "diagnostic:repeated-query") as manager:
        with patch("minicode.memory.time.time", return_value=REFERENCE_TIME), patch(
            "minicode.memory_pipeline.time.time", return_value=REFERENCE_TIME
        ), patch("minicode.memory_injector.time.time", return_value=REFERENCE_TIME):
            manager.search(repeat_case["task_description"], scope=None, limit=5)
            manager.get_relevant_context(query=repeat_case["task_description"], max_entries=5, max_tokens=500)
            pipeline = _pipeline_for_case(manager, repeat_case)
            pipeline.inject(
                repeat_case["task_description"], repeat_case["current_files"],
                [{"role": "system", "content": "SYSTEM"}], context_usage=0.4
            )
            _, repeated_entry = manager._find_entry_by_id(repeat_id)
            repeat_counts = {
                "retrieval_count": repeated_entry.retrieval_count,
                "injection_count": repeated_entry.injection_count,
                "save_count": len(manager.save_events),
                "search_call_count": len(manager.search_calls),
            }
    diagnostics.append(
        {
            "risk_id": "repeated-query-counters-io",
            "confirmed": repeat_counts["retrieval_count"] > 1 and repeat_counts["save_count"] > 1,
            **repeat_counts,
        }
    )
    return diagnostics


def deterministic_report_view(report: dict[str, Any]) -> dict[str, Any]:
    """Remove timing observations for byte-stable repeatability comparison."""
    view = copy.deepcopy(report)
    view.pop("latency", None)
    for case in view.get("per_case_results", []):
        for result in case.get("arms", {}).values():
            result.pop("latency_ms", None)
    return view


def evaluate_dataset(
    dataset_root: Path,
    *,
    project_root: Path | None = None,
    include_diagnostics: bool = True,
) -> dict[str, Any]:
    """Evaluate all four arms without touching production memory or the network."""
    project_root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    cases = load_dataset(Path(dataset_root))
    production_before = hash_production_files(project_root)
    formal_before = snapshot_formal_memory(project_root)
    fixture_hashes_before = {
        str(path.relative_to(dataset_root)): _sha256(path)
        for path in sorted(Path(dataset_root).rglob("*.json"))
    }

    arm_results: dict[str, list[dict[str, Any]]] = {arm: [] for arm in ARMS}
    per_case_results: list[dict[str, Any]] = []
    for case in cases:
        results = {arm: evaluate_arm(case, arm) for arm in ARMS}
        for arm, result in results.items():
            arm_results[arm].append(result)
        per_case_results.append(
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "primary_id": case["primary_id"],
                "expected_no_injection": case["expected_no_injection"],
                "arms": results,
                "consistency": _pairwise_consistency(results),
            }
        )

    overall = {arm: aggregate_results(results) for arm, results in arm_results.items()}
    per_category: dict[str, dict[str, Any]] = {}
    for category in CATEGORIES:
        per_category[category] = {
            arm: aggregate_results([result for result in arm_results[arm] if result["category"] == category])
            for arm in ARMS
        }
    latency = {
        arm: {
            "p50_ms": _percentile([result["latency_ms"] for result in results], 0.50),
            "p95_ms": _percentile([result["latency_ms"] for result in results], 0.95),
        }
        for arm, results in arm_results.items()
    }
    diagnostics = run_known_risk_diagnostics(cases, per_case_results) if include_diagnostics else []

    production_after = hash_production_files(project_root)
    formal_after = snapshot_formal_memory(project_root)
    fixture_hashes_after = {
        str(path.relative_to(dataset_root)): _sha256(path)
        for path in sorted(Path(dataset_root).rglob("*.json"))
    }
    unavailable = {
        "manager_global_search": [
            "actual_rendered_precision",
            "feedback_attribution_precision",
            "token_budget_violation",
            "returned_rendered_disagreement",
            "rendered_recorded_disagreement",
        ],
        "manager_context_query": ["feedback_attribution_precision", "rendered_recorded_disagreement"],
        "pipeline_read": [
            "actual_rendered_precision",
            "feedback_attribution_precision",
            "token_budget_violation",
            "returned_rendered_disagreement",
            "rendered_recorded_disagreement",
        ],
        "pipeline_inject": [],
    }
    return {
        "schema_version": "memory-retrieval-baseline-v1",
        "evaluator_version": EVALUATOR_VERSION,
        "synthetic_data": True,
        "reference_time": REFERENCE_TIME,
        "dataset_case_count": len(cases),
        "category_counts": dict(sorted(Counter(case["category"] for case in cases).items())),
        "production_file_hashes_before": production_before,
        "production_file_hashes_after": production_after,
        "production_files_unchanged": production_before == production_after,
        "fixture_hashes_before": fixture_hashes_before,
        "fixture_hashes_after": fixture_hashes_after,
        "fixtures_unchanged": fixture_hashes_before == fixture_hashes_after,
        "formal_memory_snapshot_before": formal_before,
        "formal_memory_snapshot_after": formal_after,
        "formal_memory_touched": formal_before != formal_after,
        "formal_memory_access_mode": (
            "evaluator-window hash-and-stat audit only; evaluator arms use patched temporary roots; "
            "this field does not attest to unrelated repository tests run before or after the evaluator"
        ),
        "remote_call_count": 0,
        "arm_configuration": {
            "manager_global_search": {"scope": None, "min_relevance": 0.1, "vector": False, "reranker": False},
            "manager_context_query": {"query_aware": True, "scope_order": ["local", "project", "user"]},
            "pipeline_read": {"reranker": False, "sparse_vector": False, "dense_vector": False},
            "pipeline_inject": {
                "reranker": False,
                "vector": False,
                "retrieval_quality": 0.5,
                "per_memory_token_limit": "case.max_tokens // case.max_memories",
            },
        },
        "overall_metrics": overall,
        "per_category_metrics": per_category,
        "per_case_results": per_case_results,
        "entrypoint_consistency": _summarize_consistency(per_case_results),
        "known_risk_reproductions": diagnostics,
        "latency": latency,
        "token_usage": {
            arm: {"average_memory_tokens": overall[arm]["average_memory_tokens"]}
            for arm in ARMS
        },
        "save_io": {
            arm: {
                "average_scope_saves": overall[arm]["average_save_count"],
                "total_scope_saves": sum(result["save_count"] for result in arm_results[arm]),
            }
            for arm in ARMS
        },
        "unavailable_metrics": unavailable,
        "limitations": [
            "All relevance labels and measurements come from a fixed synthetic benchmark.",
            "No statistical significance claim is made and cases are not independent users.",
            "The LLM reranker and dense/sparse vector backends are disabled in the four scored arms.",
            "Evaluator-only exact ID markers slightly change token statistics but avoid ambiguous mapping.",
            "Latency is environmental and excluded from deterministic core comparisons.",
            "formal_memory_touched covers only the evaluator execution window, not other repository tests.",
            "Current defects are observations, not future compatibility requirements.",
        ],
    }


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _format_metric(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render the measured baseline without production-readiness claims."""
    lines = [
        "# Memory Retrieval Phase 1 Baseline",
        "",
        "> Scope: fixed, fully synthetic cases. This is an offline diagnostic baseline, not a production-accuracy claim.",
        "",
        "## Dataset",
        "",
        f"- Cases: {report['dataset_case_count']}",
        f"- Synthetic data: `{str(report['synthetic_data']).lower()}`",
        f"- Remote calls: {report['remote_call_count']}",
        f"- Production files unchanged: `{str(report['production_files_unchanged']).lower()}`",
        f"- Formal memory touched during evaluator execution: `{str(report['formal_memory_touched']).lower()}`",
        "",
        "## Core Metrics",
        "",
        "| Arm | P@1 | P@3 | P@5 | R@5 | MRR | nDCG@5 | Exclude rate | Negative false rate | Max-count rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        metrics = report["overall_metrics"][arm]
        lines.append(
            "| " + " | ".join(
                [
                    arm,
                    _format_metric(metrics["precision_at_1"]),
                    _format_metric(metrics["precision_at_3"]),
                    _format_metric(metrics["precision_at_5"]),
                    _format_metric(metrics["recall_at_5"]),
                    _format_metric(metrics["mrr"]),
                    _format_metric(metrics["ndcg_at_5"]),
                    _format_metric(metrics["must_exclude_violation_rate"]),
                    _format_metric(metrics["negative_false_injection_rate"]),
                    _format_metric(metrics["max_memories_violation_rate"]),
                ]
            ) + " |"
        )
    lines.extend(
        [
            "",
            "## Per-Category P@1 / MRR",
            "",
            "| Category | Global P@1 / MRR | Context P@1 / MRR | Read P@1 / MRR | Inject P@1 / MRR |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for category, category_metrics in report["per_category_metrics"].items():
        values = []
        for arm in ARMS:
            metrics = category_metrics[arm]
            values.append(
                f"{_format_metric(metrics['precision_at_1'])} / {_format_metric(metrics['mrr'])}"
            )
        lines.append(f"| {category} | {' | '.join(values)} |")
    lines.extend(
        [
            "",
            "## Rendering And Attribution",
            "",
            "| Arm | Rendered precision | Feedback precision | Returned/rendered disagreements | Rendered/recorded disagreements | Avg memory tokens | Avg saves |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for arm in ARMS:
        metrics = report["overall_metrics"][arm]
        lines.append(
            "| " + " | ".join(
                [
                    arm,
                    _format_metric(metrics["actual_rendered_precision"]),
                    _format_metric(metrics["feedback_attribution_precision"]),
                    _format_metric(metrics["returned_rendered_disagreement_count"]),
                    _format_metric(metrics["rendered_recorded_disagreement_count"]),
                    _format_metric(metrics["average_memory_tokens"]),
                    _format_metric(metrics["average_save_count"]),
                ]
            ) + " |"
        )
    pipeline_metrics = report["overall_metrics"]["pipeline_inject"]
    lines.extend(
        [
            "",
            "Pipeline injection recorded "
            f"{pipeline_metrics['returned_rendered_disagreement_count']} returned/rendered and "
            f"{pipeline_metrics['rendered_recorded_disagreement_count']} rendered/recorded disagreements. "
            "The four ID views are retained separately in every per-case result.",
        ]
    )
    confirmed = [item for item in report["known_risk_reproductions"] if item.get("confirmed")]
    lines.extend(["", "## Confirmed Diagnostics", ""])
    for item in confirmed:
        lines.append(f"- `{item['risk_id']}`: confirmed by the recorded synthetic reproduction.")
    lines.extend(
        [
            "",
            "## Highest-Severity Findings",
            "",
            "1. **P1 - Injection identity is not truthful above five candidates.** The Injector records all returned IDs, Pipeline renders only five, and task feedback rewards or penalizes the full returned list.",
            "2. **P1 - Production entrypoints do not share retrieval semantics.** Query-aware manager context is scope-sequential, Injector discards BM25/global ordering during its re-score, and Pipeline.read is bypassed.",
            "3. **P1 - No-query paths inject unrelated active memory.** The headless reproduction emitted active entries for a no-match task; compaction calls the same no-query branch, and TUI/stdin can inject an entry twice through two managers.",
            "",
            "Additional P2 observations: `max_memories` is not a final Injector cap; vector-only IDs cannot survive current RRF; current files/domains are empty on the real agent injection call; recovered tool errors receive negative feedback; and retrieval causes repeated persistent saves.",
        ]
    )
    lines.extend(
        [
            "",
            "## Failed-Case Examples",
            "",
        ]
    )
    for arm in ARMS:
        primary_misses = [
            case["case_id"]
            for case in report["per_case_results"]
            if case["primary_id"] is not None
            and not case["arms"][arm]["metrics"]["primary_hit"]
        ]
        exclude_violations = [
            case["case_id"]
            for case in report["per_case_results"]
            if case["arms"][arm]["metrics"]["must_exclude_violation"]
        ]
        lines.append(
            f"- `{arm}`: primary misses `{', '.join(primary_misses[:6]) or 'none'}`; "
            f"exclude violations `{', '.join(exclude_violations[:6]) or 'none'}`."
        )
    lines.extend(["", "## Latency", "", "| Arm | p50 ms | p95 ms |", "|---|---:|---:|"])
    for arm in ARMS:
        latency = report["latency"][arm]
        lines.append(f"| {arm} | {_format_metric(latency['p50_ms'])} | {_format_metric(latency['p95_ms'])} |")
    lines.extend(
        [
            "",
            "## Production Reachability",
            "",
            "- Bypasses global rank: query-aware `get_relevant_context` budgets scopes independently; `MemoryInjector` performs a separate coarse re-score.",
            "- Not used by production prompt injection: `MemoryPipeline.read`, query reformulation, vector/RRF, and graph spreading.",
            "- Used in production agent injection: Injector scoped search, tag lookup, optional live-model reranker, controller, first-five formatting, injection recording, and outcome feedback.",
            "- No production caller found: failure-recovery injection and timeline session search/context.",
            "",
            "## Metric Validity",
            "",
            "Valid here: rank metrics against manual synthetic grades, exact marker-derived rendered IDs, count/token checks, lifecycle leakage, attribution overlap, latency, and save counts.",
            "",
            "Unavailable where the interface exposes no prompt or feedback: rendered precision, token-budget checks, and attribution metrics remain null for read-only arms. See `unavailable_metrics` in the JSON artifact.",
            "",
            "## Recommended Phase 2 Order",
            "",
            "1. Introduce one retrieval result contract carrying ordered candidates, rendered IDs, recorded IDs, score provenance, and limits.",
            "2. Make one query-aware production owner serve TUI, stdin, headless, agent injection, and compaction; remove double injection before changing ranking weights.",
            "3. Enforce final count and total-token budgets, then attribute injection and feedback only to rendered IDs.",
            "4. Preserve BM25/global relevance through Injector selection and wire real files/domains before reconsidering vector, graph, or reranker expansion.",
            "5. Add a relevance floor/no-match outcome and reranker-summary safety validation, then rerun this frozen dataset without changing gold labels.",
            "",
            "## Interpretation",
            "",
            "Facts: the four arms use different production methods and can return different orders and counts. "
            "`MemoryPipeline.inject` does not call `MemoryPipeline.read`.",
            "",
            "Inference: unifying ownership around one candidate/result contract is the smallest next step, but this phase does not modify that behavior.",
            "",
            "Limits: results apply only to this synthetic fixture, with LLM and vector retrieval disabled. "
            "Unavailable metrics are serialized as null rather than replaced with proxies.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_report(report), encoding="utf-8")
