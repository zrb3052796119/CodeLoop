"""Background Memory Curator Agent — proactive memory optimization.

Unlike the reactive MemoryReranker (runs at query time), the Curator runs
during idle periods to:

1. CONSOLIDATE: Merge 3-5 related memories into a synthetic "insight"
2. VALIDATE: Cross-reference memories against codebase for staleness
3. CLEAN: Archive near-duplicate memories (Jaccard > 0.9)
4. REPORT: Generate memory health metrics

Runs every N tasks or on explicit trigger. Uses lightweight LLM (Haiku) for
consolidation, rule-based methods for validation and cleaning.

Architecture:
  CyberneticOrchestrator
    └── MemoryCuratorAgent
          ├── MemoryManager (read/write)
          ├── LLM adapter (for consolidate)
          └── Workspace access (for validate)
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import unicodedata

from minicode.logging_config import get_logger

logger = get_logger("memory_curator")


# ── Data types ─────────────────────────────────────────────────────

@dataclass
class CuratorReport:
    """Output of a curation cycle."""
    insights_created: int = 0
    memories_archived: int = 0
    memories_validated: int = 0
    stale_count: int = 0
    stale_entries: int = 0
    total_entries: int = 0
    tier_distribution: dict[str, int] = field(default_factory=dict)
    domain_distribution: dict[str, int] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    eligible_entries: int = 0
    exact_duplicate_groups: int = 0
    near_duplicate_groups: int = 0
    candidate_pairs: int = 0
    similarity_comparisons: int = 0
    stale_paths_checked: int = 0
    entries_changed: int = 0
    scopes_saved: int = 0
    status: str = "completed"
    stop_reason: str = ""
    phase_timings_ms: dict[str, float] = field(default_factory=dict)
    duplicate_candidates_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "insights_created": self.insights_created,
            "memories_archived": self.memories_archived,
            "memories_validated": self.memories_validated,
            "stale_count": self.stale_count,
            "stale_entries": self.stale_entries,
            "total_entries": self.total_entries,
            "tier_distribution": self.tier_distribution,
            "domain_distribution": self.domain_distribution,
            "recommendations": self.recommendations,
            "duration_ms": round(self.duration_ms, 1),
            "eligible_entries": self.eligible_entries,
            "exact_duplicate_groups": self.exact_duplicate_groups,
            "near_duplicate_groups": self.near_duplicate_groups,
            "candidate_pairs": self.candidate_pairs,
            "similarity_comparisons": self.similarity_comparisons,
            "stale_paths_checked": self.stale_paths_checked,
            "entries_changed": self.entries_changed,
            "scopes_saved": self.scopes_saved,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "phase_timings_ms": {
                name: round(value, 1)
                for name, value in self.phase_timings_ms.items()
            },
            "duplicate_candidates_truncated": self.duplicate_candidates_truncated,
        }


@dataclass(frozen=True)
class _DuplicateSignature:
    index: int
    entry: Any
    normalized: str
    digest: str
    tokens: frozenset[str]
    token_count: int
    partition: tuple[str, str, str, str]


@dataclass
class _DuplicatePlan:
    groups: list[list[Any]] = field(default_factory=list)
    exact_groups: int = 0
    near_groups: int = 0
    candidate_pairs: int = 0
    similarity_comparisons: int = 0
    eligible_entries: int = 0
    partial: bool = False
    stop_reason: str = ""


@dataclass
class _PathStatus:
    exists: bool | None
    checked: bool = False


_MARKDOWN_FORMAT_RE = re.compile(r"(`+|[*_#>\[\]()]|^-+\s*)", re.M)
_PATH_RE = re.compile(r"(?<![\w/.-])([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]{1,12})(?![\w/.-])")
_MAX_PATHS_PER_ENTRY = 12
_MAX_PATH_LENGTH = 240


# ── Consolidation prompt ───────────────────────────────────────────

CONSOLIDATE_PROMPT = """Synthesize a concise insight from these related project memories:

{memory_texts}

Create a SINGLE insight sentence that captures the common pattern, rule, or knowledge across these memories. The insight should be specific enough to guide an AI agent.

Format: Return just the insight sentence, nothing else."""


# ── Curator Agent ───────────────────────────────────────────────────

class MemoryCuratorAgent:
    """Background agent that proactively optimizes the memory store.

    Usage:
        curator = MemoryCuratorAgent(memory_mgr, model_adapter, workspace_path)
        report = curator.run_cycle()  # Call during idle or every N tasks
    """

    def __init__(
        self,
        memory_manager: Any | None = None,
        model_adapter: Any | None = None,
        workspace_path: str | None = None,
        min_similarity_consolidate: float = 0.6,
        min_similarity_archive: float = 0.9,
        max_insights_per_cycle: int = 3,
        run_interval_tasks: int = 10,
        max_candidate_pairs: int = 2_000_000,
        max_similarity_comparisons: int = 2_000_000,
    ):
        self._memory = memory_manager
        self._model = model_adapter
        self._workspace = workspace_path
        self._min_sim_consolidate = min_similarity_consolidate
        self._min_sim_archive = min_similarity_archive
        self._max_insights = max_insights_per_cycle
        self._run_interval = run_interval_tasks
        self._max_candidate_pairs = max_candidate_pairs
        self._max_similarity_comparisons = max_similarity_comparisons

        self._task_count = 0
        self._last_run: float = 0.0
        self._report_history: list[CuratorReport] = []
        self._path_status_cache: dict[str, _PathStatus] = {}

    @property
    def should_run(self) -> bool:
        """Check if curator should run based on task count."""
        return self._task_count >= self._run_interval

    def on_task_complete(self) -> None:
        """Notify curator that a task completed. Increments counter."""
        self._task_count += 1

    def run_cycle(self, force: bool = False) -> CuratorReport:
        """Execute a full curation cycle.

        Args:
            force: If True, run even if task threshold not met.

        Returns:
            CuratorReport with cycle metrics.
        """
        if not force and not self.should_run:
            return CuratorReport()

        if self._memory is None:
            return CuratorReport()

        if (
            hasattr(self._memory, "coordinated_write")
            and not getattr(self._memory, "in_write_transaction", False)
        ):
            from minicode.memory import MemoryScope

            return self._memory.coordinated_write(
                tuple(MemoryScope), lambda: self.run_cycle(force=force)
            )

        start = time.time()
        report = CuratorReport()
        self._task_count = 0
        self._path_status_cache = {}

        try:
            # 1. Collect stats
            report = self._timed_phase(report, "collect_stats", self._collect_stats, report)

            # 2. Archive exact and near-duplicates
            archived = self._timed_phase(report, "duplicates", self._archive_duplicates, report)
            report.memories_archived = archived

            # 3. Validate against codebase
            if self._workspace:
                stale, validated = self._timed_phase(report, "stale_paths", self._validate_memories, report)
                report.stale_count = stale
                report.stale_entries = stale
                report.memories_validated = validated

            # 4. Consolidate related memories into insights
            insights = self._timed_phase(report, "insights", self._consolidate_insights, report)
            report.insights_created = insights

            # 5. Run tier promotion
            if hasattr(self._memory, 'promote_memories'):
                try:
                    self._timed_phase(report, "promotion", self._memory.promote_memories)
                except Exception as exc:
                    report.status = "failed"
                    report.stop_reason = f"promotion failed: {type(exc).__name__}"
                    raise

            # 6. Run link creation using the same candidate filter as duplicate
            # detection; the legacy MemoryManager.link_memories path remains for
            # small direct calls but is too expensive for Curator cycles.
            self._timed_phase(report, "linking", self._link_memories_optimized, report)
        except Exception as exc:
            if report.status != "failed":
                report.status = "failed"
                report.stop_reason = f"{type(exc).__name__}: {exc}"
            logger.warning("Curator cycle failed: %s", report.stop_reason)

        report.duration_ms = (time.time() - start) * 1000
        report.timestamp = time.time()
        self._report_history.append(report)
        self._last_run = time.time()

        logger.info(
            "Curator: status=%s insights=%d archived=%d stale=%d total=%d "
            "candidates=%d comparisons=%d saves=%d %.0fms",
            report.status,
            report.insights_created, report.memories_archived,
            report.stale_count, report.total_entries,
            report.candidate_pairs, report.similarity_comparisons,
            report.scopes_saved, report.duration_ms,
        )
        return report

    def _timed_phase(self, report: CuratorReport, name: str, func, *args):
        start = time.perf_counter()
        result = func(*args)
        report.phase_timings_ms[name] = (time.perf_counter() - start) * 1000
        return result

    # ── Stats collection ───────────────────────────────────────

    def _collect_stats(self, report: CuratorReport) -> CuratorReport:
        from minicode.memory import MemoryScope
        total = 0
        tiers: Counter[str] = Counter()
        domains: Counter[str] = Counter()

        for scope in MemoryScope:
            if scope not in self._memory.memories:
                continue
            for entry in self._memory.memories[scope].entries:
                total += 1
                tiers[entry.tier.value] += 1
                for d in entry.domains:
                    domains[d] += 1

        report.total_entries = total
        report.tier_distribution = dict(tiers)
        report.domain_distribution = dict(domains)

        if total > 0:
            recs = []
            archive_pct = tiers.get("archival", 0) / total
            if archive_pct > 0.5:
                recs.append(f"High archival ratio ({archive_pct:.0%}), consider purge")
            if len(domains) < 2:
                recs.append("Low domain diversity, consider broader knowledge capture")
            report.recommendations = recs

        return report

    # ── Duplicate archiving ─────────────────────────────────────

    @staticmethod
    def normalize_duplicate_content(content: str) -> str:
        """Normalize content for exact duplicate bucketing.

        This intentionally removes lightweight Markdown formatting but keeps
        substantive words, numbers, Unicode text, and paths.
        """
        if not content:
            return ""
        normalized = unicodedata.normalize("NFKC", content)
        normalized = _MARKDOWN_FORMAT_RE.sub(" ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip().lower()

    @staticmethod
    def _stable_digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _token_set(text: str) -> frozenset[str]:
        from minicode.memory import _tokenize
        tokens = []
        for token in _tokenize(text):
            if not token or len(token) > 128:
                continue
            tokens.append(token)
        return frozenset(tokens)

    @staticmethod
    def _entry_partition(entry: Any) -> tuple[str, str, str, str]:
        return (
            str(getattr(entry, "approval_status", "")),
            str(getattr(entry, "safety_status", "")),
            str(getattr(entry, "lifecycle_status", "")),
            str(getattr(entry, "scope", "")),
        )

    def _build_duplicate_signatures(self, entries: list[Any]) -> list[_DuplicateSignature]:
        signatures: list[_DuplicateSignature] = []
        for index, entry in enumerate(entries):
            normalized = self.normalize_duplicate_content(getattr(entry, "content", ""))
            if not normalized:
                continue
            tokens = self._token_set(normalized)
            signatures.append(
                _DuplicateSignature(
                    index=index,
                    entry=entry,
                    normalized=normalized,
                    digest=self._stable_digest(normalized),
                    tokens=tokens,
                    token_count=len(tokens),
                    partition=self._entry_partition(entry),
                )
            )
        return signatures

    def _exact_duplicate_groups(
        self,
        signatures: list[_DuplicateSignature],
    ) -> list[list[Any]]:
        buckets: dict[tuple[tuple[str, str, str, str], str], list[_DuplicateSignature]] = {}
        for signature in signatures:
            buckets.setdefault((signature.partition, signature.digest), []).append(signature)

        groups: list[list[Any]] = []
        for bucket in buckets.values():
            if len(bucket) < 2:
                continue
            confirmed: dict[str, list[_DuplicateSignature]] = {}
            for signature in bucket:
                # Confirm normalized equality inside hash bucket so theoretical
                # hash collision cannot produce a false duplicate.
                confirmed.setdefault(signature.normalized, []).append(signature)
            for signatures_for_content in confirmed.values():
                if len(signatures_for_content) >= 2:
                    groups.append([s.entry for s in signatures_for_content])
        return self._sort_groups(groups)

    @staticmethod
    def _sort_groups(groups: list[list[Any]]) -> list[list[Any]]:
        return sorted(
            [sorted(group, key=lambda e: (getattr(e, "created_at", 0.0), getattr(e, "id", ""))) for group in groups],
            key=lambda group: getattr(group[0], "id", "") if group else "",
        )

    def _prefix_length(self, token_count: int, threshold: float) -> int:
        if token_count <= 0:
            return 0
        return max(1, token_count - math.ceil(threshold * token_count) + 1)

    def _candidate_pairs(
        self,
        signatures: list[_DuplicateSignature],
        threshold: float,
        *,
        max_pairs: int,
    ) -> tuple[set[tuple[int, int]], bool, str]:
        """Generate deterministic Jaccard candidates using prefix filtering."""
        usable = [s for s in signatures if s.token_count > 0]
        if len(usable) < 2:
            return set(), False, ""

        doc_freq: Counter[str] = Counter()
        for signature in usable:
            doc_freq.update(signature.tokens)

        ordered_tokens: dict[int, list[str]] = {}
        for signature in usable:
            ordered_tokens[signature.index] = sorted(
                signature.tokens,
                key=lambda token: (doc_freq[token], token),
            )

        candidates: set[tuple[int, int]] = set()
        postings: dict[tuple[tuple[str, str, str, str], str], list[_DuplicateSignature]] = {}
        for signature in sorted(
            usable,
            key=lambda s: (s.token_count, getattr(s.entry, "created_at", 0.0), getattr(s.entry, "id", "")),
        ):
            prefix = ordered_tokens[signature.index][: self._prefix_length(signature.token_count, threshold)]
            seen_for_entry: set[int] = set()
            for token in prefix:
                key = (signature.partition, token)
                for other in postings.get(key, []):
                    if other.index in seen_for_entry:
                        continue
                    seen_for_entry.add(other.index)
                    min_len = min(signature.token_count, other.token_count)
                    max_len = max(signature.token_count, other.token_count)
                    if max_len == 0 or min_len / max_len < threshold:
                        continue
                    pair = (min(signature.index, other.index), max(signature.index, other.index))
                    candidates.add(pair)
                    if len(candidates) > max_pairs:
                        return candidates, True, f"candidate pair budget exceeded ({max_pairs})"
                postings.setdefault(key, []).append(signature)
        return candidates, False, ""

    def _near_duplicate_groups(
        self,
        signatures: list[_DuplicateSignature],
        *,
        threshold: float,
        max_pairs: int,
        max_comparisons: int,
    ) -> tuple[list[list[Any]], int, int, bool, str]:
        candidates, partial, stop_reason = self._candidate_pairs(
            signatures,
            threshold,
            max_pairs=max_pairs,
        )
        if partial:
            return [], len(candidates), 0, True, stop_reason

        by_index = {signature.index: signature for signature in signatures}
        parent: dict[int, int] = {signature.index: signature.index for signature in signatures}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

        comparisons = 0
        for i, j in sorted(candidates):
            if comparisons >= max_comparisons:
                return [], len(candidates), comparisons, True, (
                    f"similarity comparison budget exceeded ({max_comparisons})"
                )
            a = by_index[i]
            b = by_index[j]
            comparisons += 1
            intersection = len(a.tokens & b.tokens)
            union_size = len(a.tokens | b.tokens)
            similarity = intersection / union_size if union_size else 0.0
            if similarity >= threshold:
                union(i, j)

        grouped: dict[int, list[Any]] = {}
        for signature in signatures:
            grouped.setdefault(find(signature.index), []).append(signature.entry)
        groups = [group for group in grouped.values() if len(group) >= 2]
        return self._sort_groups(groups), len(candidates), comparisons, False, ""

    def _similar_entry_pairs(
        self,
        signatures: list[_DuplicateSignature],
        *,
        threshold: float,
        max_pairs: int,
        max_comparisons: int,
    ) -> tuple[list[tuple[Any, Any]], int, int, bool, str]:
        candidates, partial, stop_reason = self._candidate_pairs(
            signatures,
            threshold,
            max_pairs=max_pairs,
        )
        if partial:
            return [], len(candidates), 0, True, stop_reason
        by_index = {signature.index: signature for signature in signatures}
        pairs: list[tuple[Any, Any]] = []
        comparisons = 0
        for i, j in sorted(candidates):
            if comparisons >= max_comparisons:
                return pairs, len(candidates), comparisons, True, (
                    f"similarity comparison budget exceeded ({max_comparisons})"
                )
            a = by_index[i]
            b = by_index[j]
            comparisons += 1
            union_size = len(a.tokens | b.tokens)
            similarity = len(a.tokens & b.tokens) / union_size if union_size else 0.0
            if similarity >= threshold:
                pairs.append((a.entry, b.entry))
        return pairs, len(candidates), comparisons, False, ""

    def _canonical_sort_key(self, entry: Any) -> tuple[Any, ...]:
        metadata = getattr(entry, "metadata", {}) or {}
        provenance = getattr(entry, "provenance", {}) or {}
        source = str(getattr(entry, "source", "") or "")
        verified_source = source in {"reflection", "curator", "test", "import"}
        return (
            0 if getattr(entry, "approval_status", "") == "approved" and getattr(entry, "safety_status", "") == "safe" and getattr(entry, "is_active", False) else 1,
            0 if getattr(entry, "lifecycle_status", "") == "active" else 1,
            0 if not getattr(entry, "curator_locked", False) else 1,
            -int(getattr(entry, "success_count", 0) or 0),
            -float(getattr(entry, "usefulness_score", 0.0) or 0.0),
            0 if verified_source else 1,
            -(len(metadata) + len(provenance) + len(getattr(entry, "tags", []) or []) + len(getattr(entry, "domains", []) or [])),
            -len(str(getattr(entry, "content", "") or "")),
            float(getattr(entry, "created_at", 0.0) or 0.0),
            str(getattr(entry, "id", "")),
        )

    def _choose_canonical(self, group: list[Any]) -> Any:
        return sorted(group, key=self._canonical_sort_key)[0]

    def _duplicate_plan_for_scope(self, entries: list[Any]) -> _DuplicatePlan:
        active_entries = [entry for entry in entries if getattr(entry, "is_active", True)]
        signatures = self._build_duplicate_signatures(active_entries)
        exact_groups = self._exact_duplicate_groups(signatures)
        exact_archived_ids = {
            getattr(entry, "id", "")
            for group in exact_groups
            for entry in group
            if entry is not self._choose_canonical(group)
        }
        remaining_signatures = [
            signature
            for signature in signatures
            if getattr(signature.entry, "id", "") not in exact_archived_ids
        ]
        near_groups, candidate_pairs, comparisons, partial, stop_reason = self._near_duplicate_groups(
            remaining_signatures,
            threshold=self._min_sim_archive,
            max_pairs=self._max_candidate_pairs,
            max_comparisons=self._max_similarity_comparisons,
        )
        return _DuplicatePlan(
            groups=exact_groups + near_groups,
            exact_groups=len(exact_groups),
            near_groups=len(near_groups),
            candidate_pairs=candidate_pairs,
            similarity_comparisons=comparisons,
            eligible_entries=len(active_entries),
            partial=partial,
            stop_reason=stop_reason,
        )

    def _record_curator_audit(
        self,
        scope: Any,
        entry: Any,
        *,
        action: str,
        previous_approval: str,
        previous_lifecycle: str,
        reason: str,
        defer_save: bool = True,
    ) -> None:
        if not hasattr(self._memory, "_append_approval_audit"):
            return
        from minicode.memory import MemorySafetyResult

        safety = MemorySafetyResult(
            str(getattr(entry, "safety_status", "safe") or "safe"),
            str(getattr(entry, "safety_reason", "") or ""),
            "low",
        )
        try:
            self._memory._append_approval_audit(
                scope,
                entry,
                action=action,
                actor="curator",
                previous_approval=previous_approval,
                previous_lifecycle=previous_lifecycle,
                reason=reason,
                safety=safety,
                extra={"curator": True},
                save=not defer_save,
            )
        except TypeError:
            self._memory._append_approval_audit(
                scope,
                entry,
                action=action,
                actor="curator",
                previous_approval=previous_approval,
                previous_lifecycle=previous_lifecycle,
                reason=reason,
                safety=safety,
                extra={"curator": True},
            )

    def _archive_duplicate_entry(self, scope: Any, entry: Any, reason: str) -> bool:
        from minicode.memory import MemoryTier

        if (
            getattr(entry, "tier", None) == MemoryTier.ARCHIVAL
            and getattr(entry, "lifecycle_status", "") == "deprecated"
            and getattr(entry, "curator_locked", False)
            and getattr(entry, "tier_reason", "") == reason
        ):
            return False

        previous_approval = str(getattr(entry, "approval_status", ""))
        previous_lifecycle = str(getattr(entry, "lifecycle_status", ""))
        entry.tier = MemoryTier.ARCHIVAL
        entry.lifecycle_status = "deprecated"
        entry.tier_reason = reason
        entry.deprecated_at = time.time()
        entry.curator_locked = True
        if hasattr(entry, "invalidate_tokens"):
            entry.invalidate_tokens()
        self._record_curator_audit(
            scope,
            entry,
            action="curator_duplicate_archive",
            previous_approval=previous_approval,
            previous_lifecycle=previous_lifecycle,
            reason=reason,
        )
        return True

    def _archive_duplicates(self, report: CuratorReport | None = None) -> int:
        from minicode.memory import MemoryEntry, MemoryScope
        archived = 0
        touched: set[MemoryScope] = set()
        snapshots: dict[MemoryScope, dict[str, dict[str, Any]]] = {}
        for scope in MemoryScope:
            if scope not in self._memory.memories:
                continue
            memory_file = self._memory.memories[scope]
            plan = self._duplicate_plan_for_scope(memory_file.entries)
            if report is not None:
                report.eligible_entries += plan.eligible_entries
                report.exact_duplicate_groups += plan.exact_groups
                report.near_duplicate_groups += plan.near_groups
                report.candidate_pairs += plan.candidate_pairs
                report.similarity_comparisons += plan.similarity_comparisons
                if plan.partial:
                    report.status = "partial"
                    report.stop_reason = plan.stop_reason
                    report.duplicate_candidates_truncated = True

            archived_ids: set[str] = set()
            for group in plan.groups:
                canonical = self._choose_canonical(group)
                for entry in group:
                    entry_id = str(getattr(entry, "id", ""))
                    if entry is canonical or entry_id in archived_ids:
                        continue
                    snapshots.setdefault(scope, {}).setdefault(entry_id, entry.to_dict())
                    if self._archive_duplicate_entry(scope, entry, "duplicate"):
                        archived += 1
                        archived_ids.add(entry_id)
                        touched.add(scope)

        try:
            for scope in touched:
                if hasattr(self._memory.memories[scope], "_rebuild_indices"):
                    self._memory.memories[scope]._rebuild_indices()
                if hasattr(self._memory, "_save_approval_audit"):
                    self._memory._save_approval_audit(scope)
                if hasattr(self._memory, "_save_scope"):
                    self._memory._save_scope(scope)
                    if report is not None:
                        report.scopes_saved += 1
        except Exception:
            for scope, scope_snapshots in snapshots.items():
                memory_file = self._memory.memories[scope]
                memory_file._ensure_cache_valid()
                for entry_id, data in scope_snapshots.items():
                    entry = memory_file._id_index.get(entry_id)
                    if entry is None:
                        continue
                    restored = MemoryEntry.from_dict(data)
                    entry.__dict__.update(restored.__dict__)
                memory_file._rebuild_indices()
            raise
        if report is not None:
            report.entries_changed += archived
        return archived

    # ── Codebase validation ────────────────────────────────────

    def _workspace_root(self) -> Path | None:
        if not self._workspace:
            return None
        try:
            return Path(self._workspace).resolve()
        except OSError:
            return None

    def _extract_entry_paths(self, content: str) -> list[str]:
        paths: list[str] = []
        for match in _PATH_RE.findall(content or ""):
            candidate = match.strip(".,;:()[]{}'\"")
            if not candidate or len(candidate) > _MAX_PATH_LENGTH:
                continue
            if any(ord(ch) < 32 for ch in candidate):
                continue
            paths.append(candidate)
            if len(paths) >= _MAX_PATHS_PER_ENTRY:
                break
        return list(dict.fromkeys(paths))

    def _normalize_workspace_path(self, raw_path: str) -> str | None:
        root = self._workspace_root()
        if root is None:
            return None
        if raw_path.startswith(("http://", "https://")):
            return None
        try:
            path = Path(raw_path)
            if path.is_absolute():
                # Do not inspect arbitrary absolute paths outside workspace.
                resolved = path.resolve(strict=False)
            else:
                resolved = (root / raw_path.lstrip("/\\")).resolve(strict=False)
            if root != resolved and root not in resolved.parents:
                return None
            return str(resolved)
        except (OSError, RuntimeError, ValueError):
            return None

    def _path_status(self, normalized_path: str) -> _PathStatus:
        cached = self._path_status_cache.get(normalized_path)
        if cached is not None:
            return cached
        try:
            exists = Path(normalized_path).exists()
        except PermissionError:
            exists = None
        except OSError:
            exists = None
        status = _PathStatus(exists=exists, checked=True)
        self._path_status_cache[normalized_path] = status
        return status

    def _mark_stale_entry(self, scope: Any, entry: Any) -> bool:
        from minicode.memory import (
            MemoryTier,
            _APPROVAL_PENDING,
            _APPROVAL_REJECTED,
            _SAFETY_UNSAFE,
            _approval_hash_for_entry,
            assess_memory_safety,
        )

        already_locked = (
            getattr(entry, "tier", None) == MemoryTier.ARCHIVAL
            and getattr(entry, "lifecycle_status", "") == "deprecated"
            and getattr(entry, "curator_locked", False)
            and getattr(entry, "tier_reason", "") == "stale_reference"
        )
        if already_locked:
            return False

        previous_approval = str(getattr(entry, "approval_status", ""))
        previous_lifecycle = str(getattr(entry, "lifecycle_status", ""))
        entry.tier = MemoryTier.ARCHIVAL
        entry.lifecycle_status = "deprecated"
        entry.tier_reason = "stale_reference"
        entry.deprecated_at = time.time()
        entry.curator_locked = True
        if not str(getattr(entry, "content", "")).startswith("[DEPRECATED:"):
            entry.content = (
                "[DEPRECATED: referenced files no longer exist] "
                + str(getattr(entry, "content", ""))[:500]
            )
        safety = assess_memory_safety(getattr(entry, "content", ""), source="curator")
        entry.safety_status = safety.status
        entry.safety_reason = safety.reason
        entry.approval_status = (
            _APPROVAL_REJECTED if safety.status == _SAFETY_UNSAFE else _APPROVAL_PENDING
        )
        entry.approval_reason = "curator stale rewrite requires restore"
        entry.approval_actor = "curator"
        entry.approval_decided_at = time.time()
        entry.approval_content_hash = _approval_hash_for_entry(entry)
        if hasattr(entry, "invalidate_tokens"):
            entry.invalidate_tokens()
        self._record_curator_audit(
            scope,
            entry,
            action="curator_stale_archive",
            previous_approval=previous_approval,
            previous_lifecycle=previous_lifecycle,
            reason="stale_reference",
        )
        return True

    def _validate_memories(self, report: CuratorReport | None = None) -> tuple[int, int]:
        """Check if memory-referenced files/patterns still exist in workspace."""
        if not self._workspace:
            return 0, 0

        from minicode.memory import MemoryScope

        stale = 0
        validated = 0
        changed = 0
        touched: set[MemoryScope] = set()

        for scope in MemoryScope:
            if scope not in self._memory.memories:
                continue
            for entry in self._memory.memories[scope].entries:
                paths = self._extract_entry_paths(getattr(entry, "content", ""))
                if not paths:
                    continue
                normalized_paths = [
                    normalized
                    for raw in paths
                    if (normalized := self._normalize_workspace_path(raw)) is not None
                ]
                if not normalized_paths:
                    continue
                validated += 1
                statuses = [self._path_status(path) for path in normalized_paths]
                if report is not None:
                    report.stale_paths_checked = len(self._path_status_cache)
                any_exists = any(status.exists is True for status in statuses)
                any_unknown = any(status.exists is None for status in statuses)
                all_missing = not any_exists and not any_unknown
                if all_missing and normalized_paths:
                    if self._mark_stale_entry(scope, entry):
                        touched.add(scope)
                        changed += 1
                    stale += 1

        for scope in touched:
            if hasattr(self._memory.memories[scope], "_rebuild_indices"):
                self._memory.memories[scope]._rebuild_indices()
            if hasattr(self._memory, "_save_approval_audit"):
                self._memory._save_approval_audit(scope)
            if hasattr(self._memory, "_save_scope"):
                self._memory._save_scope(scope)
                if report is not None:
                    report.scopes_saved += 1

        if report is not None:
            report.entries_changed += changed
            report.stale_entries = stale
        return stale, validated

    def _link_memories_optimized(self, report: CuratorReport | None = None) -> int:
        """Create related_to links without all-pairs comparison."""
        from minicode.memory import MemoryScope

        links = 0
        touched: set[MemoryScope] = set()
        changed_entry_ids: set[str] = set()
        max_link_candidates = min(50_000, max(1_000, self._max_candidate_pairs // 6))
        max_link_comparisons = min(50_000, max(1_000, self._max_similarity_comparisons // 6))
        max_links_per_entry = 8
        for scope in MemoryScope:
            if scope not in self._memory.memories:
                continue
            entries = [
                entry
                for entry in self._memory.memories[scope].entries
                if getattr(entry, "is_active", True)
            ]
            signatures = self._build_duplicate_signatures(entries)
            candidates, truncated, stop_reason = self._candidate_pairs(
                signatures,
                0.4,
                max_pairs=max_link_candidates,
            )
            by_index = {signature.index: signature for signature in signatures}
            comparisons = 0
            link_degree: Counter[str] = Counter({
                str(getattr(signature.entry, "id", "")): min(
                    len(getattr(signature.entry, "related_to", []) or []),
                    max_links_per_entry,
                )
                for signature in signatures
            })
            pairs: list[tuple[Any, Any]] = []
            for i, j in sorted(candidates):
                if comparisons >= max_link_comparisons:
                    truncated = True
                    stop_reason = f"link comparison budget exceeded ({max_link_comparisons})"
                    break
                a = by_index[i]
                b = by_index[j]
                a_id = str(getattr(a.entry, "id", ""))
                b_id = str(getattr(b.entry, "id", ""))
                if link_degree[a_id] >= max_links_per_entry or link_degree[b_id] >= max_links_per_entry:
                    continue
                comparisons += 1
                union_size = len(a.tokens | b.tokens)
                similarity = len(a.tokens & b.tokens) / union_size if union_size else 0.0
                if similarity >= 0.4:
                    pairs.append((a.entry, b.entry))
                    link_degree[a_id] += 1
                    link_degree[b_id] += 1
            if report is not None:
                report.candidate_pairs += min(len(candidates), max_link_candidates)
                report.similarity_comparisons += comparisons
                if truncated:
                    report.recommendations.append(
                        f"Curator linking capped for {scope.value}: {stop_reason}"
                    )
            for a, b in pairs:
                changed = False
                a_id = str(getattr(a, "id", ""))
                b_id = str(getattr(b, "id", ""))
                if b_id and b_id not in getattr(a, "related_to", []):
                    a.related_to.append(b_id)
                    changed = True
                    links += 1
                if a_id and a_id not in getattr(b, "related_to", []):
                    b.related_to.append(a_id)
                    changed = True
                    links += 1
                if changed:
                    touched.add(scope)
                    changed_entry_ids.add(a_id)
                    changed_entry_ids.add(b_id)
                    if hasattr(a, "invalidate_tokens"):
                        a.invalidate_tokens()
                    if hasattr(b, "invalidate_tokens"):
                        b.invalidate_tokens()

        for scope in touched:
            if hasattr(self._memory.memories[scope], "_rebuild_indices"):
                self._memory.memories[scope]._rebuild_indices()
            if hasattr(self._memory, "_save_scope"):
                self._memory._save_scope(scope)
                if report is not None:
                    report.scopes_saved += 1
        if report is not None:
            report.entries_changed += len(changed_entry_ids)
        return links

    # ── Insight consolidation ──────────────────────────────────

    def _consolidate_insights(self, report: CuratorReport | None = None) -> int:
        """Find related memory clusters and synthesize insights via LLM."""
        from minicode.memory import MemoryScope

        created = 0
        for scope in MemoryScope:
            if scope not in self._memory.memories or created >= self._max_insights:
                break
            entries = [
                e for e in self._memory.memories[scope].entries
                if getattr(e, "is_active", True)
            ]
            clusters = self._find_clusters(entries, report=report)
            for cluster in clusters[:self._max_insights - created]:
                before_ids = {e.id for e in self._memory.memories[scope].entries}
                insight = self._synthesize_insight(cluster)
                if insight:
                    from minicode.memory import MemoryApprovalPolicy, MemoryTier
                    entry = self._memory.add_entry(
                        scope=scope,
                        category="insight",
                        content=insight,
                        tags=["curator-insight"],
                        domains=list(set(d for e in cluster for d in e.domains)),
                        tier=MemoryTier.LONG_TERM,
                        source="curator",
                        provenance={
                            "related_to": [e.id for e in cluster],
                            "cluster_size": len(cluster),
                        },
                        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
                    )
                    if entry:
                        was_existing = entry.id in before_ids
                        entry.related_to = list(dict.fromkeys(entry.related_to + [e.id for e in cluster]))
                        if not was_existing and hasattr(self._memory, "_save_scope"):
                            self._memory._save_scope(scope)
                            if report is not None:
                                report.scopes_saved += 1
                            created += 1

        return created

    def _find_clusters(self, entries: list, report: CuratorReport | None = None) -> list[list]:
        """Find clusters of related memories using related_to + Jaccard."""
        if len(entries) < 3:
            return []

        # Use existing related_to links as seeds
        clusters: list[set[int]] = []
        seen: set[int] = set()
        id_to_index = {e.id: i for i, e in enumerate(entries)}

        for i, entry in enumerate(entries):
            if i in seen or not entry.related_to:
                continue
            cluster: set[int] = {i}
            frontier = [i]
            while frontier:
                cur = frontier.pop()
                for rid in entries[cur].related_to:
                    j = id_to_index.get(rid)
                    if j is not None and j not in cluster:
                        cluster.add(j)
                        frontier.append(j)
            if len(cluster) >= 3:
                clusters.append(cluster)
                seen |= cluster

        # Fallback: candidate-filtered Jaccard clustering for unlinked entries.
        remaining = [entry for i, entry in enumerate(entries) if i not in seen]
        signatures = self._build_duplicate_signatures(remaining)
        pairs, candidate_pairs, comparisons, partial, stop_reason = self._similar_entry_pairs(
            signatures,
            threshold=self._min_sim_consolidate,
            max_pairs=self._max_candidate_pairs,
            max_comparisons=self._max_similarity_comparisons,
        )
        if report is not None:
            report.candidate_pairs += candidate_pairs
            report.similarity_comparisons += comparisons
            if partial:
                report.status = "partial"
                report.stop_reason = stop_reason
                report.duplicate_candidates_truncated = True
        parent: dict[str, str] = {entry.id: entry.id for entry in remaining}

        def find(entry_id: str) -> str:
            while parent[entry_id] != entry_id:
                parent[entry_id] = parent[parent[entry_id]]
                entry_id = parent[entry_id]
            return entry_id

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

        for a, b in pairs:
            union(a.id, b.id)
        by_root: dict[str, set[int]] = {}
        for entry in remaining:
            group_index = id_to_index.get(entry.id)
            if group_index is not None:
                by_root.setdefault(find(entry.id), set()).add(group_index)
        for cluster in by_root.values():
            if len(cluster) >= 3:
                clusters.append(cluster)
                seen |= cluster

        return [[entries[i] for i in c] for c in clusters[:5]]

    def _synthesize_insight(self, cluster: list) -> str | None:
        """Call LLM to synthesize an insight from a memory cluster."""
        texts = "\n".join(
            f"- [{e.id}] {e.content[:150]}" for e in cluster[:5]
        )
        prompt = CONSOLIDATE_PROMPT.format(memory_texts=texts)

        try:
            if self._model and hasattr(self._model, 'generate'):
                raw = self._model.generate(prompt)
                if isinstance(raw, dict):
                    result = raw.get("content", "") or raw.get("text", "")
                else:
                    result = str(raw)
                result = result.strip()
                if 30 < len(result) < 500:
                    return result
            elif self._model and hasattr(self._model, 'next'):
                msgs = [{"role": "user", "content": prompt}]
                step = self._model.next(msgs)
                result = getattr(step, 'content', '') or ""
                result = result.strip()
                if 30 < len(result) < 500:
                    return result
        except Exception as e:
            logger.debug("Curator insight synthesis failed: %s", e)

        # Rule-based fallback
        domains = set(d for e in cluster for d in e.domains)
        common_words = self._extract_common_words([e.content for e in cluster])
        if common_words:
            return (
                f"[Auto] Memories in {', '.join(domains) or 'general'} share patterns: "
                f"{', '.join(common_words[:5])}. "
                f"({len(cluster)} related entries)"
            )
        return None

    @staticmethod
    def _extract_common_words(contents: list[str], min_len: int = 3) -> list[str]:
        """Extract common significant words across multiple texts."""
        from collections import Counter
        word_sets = []
        for c in contents:
            words = {w.lower().strip(".,;:()[]{}'\"") for w in c.split()
                    if len(w) > min_len and not w.startswith("http")}
            word_sets.append(words)
        if not word_sets:
            return []
        common = word_sets[0]
        for ws in word_sets[1:]:
            common = common & ws
        freq = Counter()
        for c in contents:
            freq.update(w.lower().strip(".,;:()[]{}'\"") for w in c.split()
                       if len(w) > min_len and w.lower().strip(".,;:()[]{}'\"") in common)
        return [w for w, _ in freq.most_common(10)]

    # ── Public API ─────────────────────────────────────────────

    def get_history(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._report_history[-10:]]

    def get_last_report(self) -> CuratorReport | None:
        return self._report_history[-1] if self._report_history else None
