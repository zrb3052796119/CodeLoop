"""Claude Code-style Context Management System for MiniCode.

Implements the three-tier context management architecture:

1. **Pre-request lightweight optimization chain**:
   - Read deduplication (hash-based file content dedup)
   - Tool result budget (large output persistence + preview replacement)
   - Time-based microcompact (old tool result cleanup)

2. **Auto Compact high-water dispatcher**:
   - Session Memory Compact (uses existing memory entries as summary base)
   - Full Compact (model-generated summary with new baseline)
   - Circuit breaker (3 consecutive failures = stop)

3. **Reactive Compact error recovery**:
   - Prompt-too-long recovery path
   - Media-size error recovery
   - Fallback to user-visible error

Architecture reference: compact(5).md (Claude Code source analysis)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from minicode.agent_budget import AgentBudgetExceeded, record_budgeted_model_call
from minicode.model_call_control import (
    ModelCallDeadlineExceeded,
    call_model_next,
    estimate_budgeted_request_input,
)
from minicode.memory_pipeline import (
    MemoryPromptReceipt,
    memory_prompt_block_proof,
    verified_materialized_memory_fragments,
    verified_receipt_memory_fragments,
)
from minicode.pricing import (
    pricing_failure_event_payload,
    project_model_cost_event,
)
from minicode.run_events import (
    emit_event_safely,
    new_model_operation_id,
    project_model_duration_ms,
    project_model_usage,
)
from minicode.turn_cancellation import TurnCancellationRequested

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core data structures
# ---------------------------------------------------------------------------


class CompactTrigger(str, Enum):
    """How the compaction was triggered."""
    MANUAL = "manual"
    AUTO = "auto"
    REACTIVE = "reactive"
    MICROCOMPACT_TIME = "microcompact_time"
    MICROCOMPACT_CACHED = "microcompact_cached"


class CompactStrategy(str, Enum):
    """Compaction strategy used."""
    SESSION_MEMORY = "session_memory"
    FULL = "full"
    PARTIAL = "partial"
    MICROCOMPACT = "microcompact"
    TOOL_BUDGET = "tool_budget"
    READ_DEDUP = "read_dedup"
    REACTIVE = "reactive"


class CompactionMaterializationState(str, Enum):
    """Whether a model-visible compaction candidate was actually installed."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    INSTALLED = "installed"
    REJECTED = "rejected"


@dataclass
class CompactBoundary:
    """Marks a compaction point in conversation history.

    After compaction, the active context view starts from the last boundary.
    ``CompactBoundary`` is control metadata; compaction also emits a separate
    model-visible summary message carrying ``_compact_boundary``.
    """
    trigger: CompactTrigger
    strategy: CompactStrategy
    timestamp: float = field(default_factory=time.time)
    tokens_before: int = 0
    tokens_after: int = 0
    messages_removed: int = 0
    logical_parent_id: str | None = None
    preserved_segment: tuple[int, int] | None = None  # (start, end) message indices kept

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger.value,
            "strategy": self.strategy.value,
            "timestamp": self.timestamp,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "messages_removed": self.messages_removed,
            "logical_parent_id": self.logical_parent_id,
            "preserved_segment": list(self.preserved_segment) if self.preserved_segment else None,
        }


@dataclass
class CompactionResult:
    """Result of a compaction operation."""
    success: bool
    strategy: CompactStrategy
    trigger: CompactTrigger
    messages: list[dict[str, Any]]
    boundary: CompactBoundary | None = None
    tokens_freed: int = 0
    summary_text: str = ""
    error: str = ""
    memory_receipt: MemoryPromptReceipt | None = None
    # A Session Memory result is only a candidate until its authority receipt
    # is revalidated at the exact prompt-install boundary.  Control-loop
    # success state must not advance while this value is PENDING.
    materialization_state: CompactionMaterializationState = (
        CompactionMaterializationState.NOT_REQUIRED
    )
    # Safe pre-Session candidate produced by tool-budget/microcompact stages.
    # If a Memory authority receipt is rejected, callers install this view so
    # already-committed optimization state still matches the live prompt.
    memory_free_fallback: list[dict[str, Any]] | None = None
    # Savings already realized before a deferred Memory block was attempted.
    # These remain truthful if the Memory receipt is later rejected.
    memory_free_tokens_freed: int = 0
    memory_free_strategy: CompactStrategy | None = None
    memory_free_messages_before: int = 0
    memory_free_messages_after: int = 0

    @property
    def effective(self) -> bool:
        return self.success and self.tokens_freed > 0

    @property
    def partial_effective(self) -> bool:
        return (
            self.materialization_state
            is CompactionMaterializationState.REJECTED
            and self.memory_free_tokens_freed > 0
        )


@dataclass
class ToolResultPersisted:
    """A tool result that was persisted to disk."""
    original_size: int
    persisted_path: Path
    preview_text: str
    tool_name: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ReadDedupEntry:
    """Tracks a file read for deduplication."""
    file_path: str
    content_hash: str
    timestamp: float
    message_index: int  # Index in messages where full content lives


@dataclass
class MicrocompactState:
    """State for microcompact operations."""
    # A fresh engine must not fire immediately: the 1h gate exists so tool
    # results still inside the provider's prompt cache are left alone. The
    # compactor is rebuilt every turn, so epoch-0 would clear results on the
    # first request of every turn.
    last_time_based_compact: float = field(default_factory=time.time)
    time_based_interval: float = 3600.0  # Default 1 hour
    keep_recent_tool_results: int = 5
    total_tokens_cleared: int = 0


@dataclass
class AutoCompactConfig:
    """Configuration for Auto Compact dispatcher."""
    enabled: bool = True
    threshold_ratio: float = 0.85  # 85% of context window
    circuit_breaker_limit: int = 3
    session_memory_enabled: bool = True
    min_keep_tokens: int = 10000  # At least 10k tokens after compact
    min_keep_messages: int = 5  # At least 5 text messages
    max_expand_tokens: int = 40000  # Max expansion for tail preservation


# ---------------------------------------------------------------------------
# Phase 2: Tool Result Budget
# ---------------------------------------------------------------------------


# Tool names are model/provider supplied text and are embedded in a file
# name. Keep the accepted alphabet narrow so a crafted name such as
# "../../escaped" can never turn a context-compaction write into a path
# traversal outside the results directory.
_TOOL_RESULT_FILENAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_SOURCE_ANCHOR_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c",
    ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt",
    ".kts", ".scala", ".sh", ".sql", ".lua", ".ex", ".exs", ".vue", ".svelte",
})
_NON_PRODUCTION_ANCHOR_PARTS = frozenset({
    "test", "tests", "__tests__", "fixtures", "__fixtures__", "docs",
    "node_modules", ".git", "vendor",
})


def _safe_tool_result_filename(tool_name: object) -> str:
    if (
        isinstance(tool_name, str)
        and _TOOL_RESULT_FILENAME_RE.fullmatch(tool_name)
    ):
        return tool_name
    return "tool"


class ToolResultBudgetManager:
    """Manages tool result size budget with disk persistence.

    When a tool_result exceeds the per-message budget, it is persisted
    to disk and replaced with a preview stub in the context.
    """

    DEFAULT_BUDGET_PER_MESSAGE = 8000  # chars per user message's tool results
    PERSIST_THRESHOLD = 4000  # Persist results larger than this
    PREVIEW_MAX_CHARS = 500
    # Newest results in a run are excluded from aggregate-budget persistence:
    # the model is actively iterating on them and they are still inside the
    # provider prompt-cache window. Mirrors MicrocompactState's
    # keep_recent_tool_results protection.
    PROTECTED_RECENT_RESULTS = 5
    # Persisted results are referenced by preview stubs in the live prompt
    # ("read this path for the full result"), so expiry must be generous.
    DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600.0
    _CLEANUP_INTERVAL_SECONDS = 3600.0

    def __init__(
        self,
        workspace: str | Path | None = None,
        budget_per_message: int = DEFAULT_BUDGET_PER_MESSAGE,
        persist_threshold: int = PERSIST_THRESHOLD,
    ):
        self._workspace = Path(workspace) if workspace else Path.cwd()
        self._budget = budget_per_message
        self._persist_threshold = persist_threshold
        self._results_dir = self._workspace / ".mini-code-tool-results"
        self._persisted: dict[str, ToolResultPersisted] = {}
        self._last_cleanup_at = 0.0
        # Short unique suffix: filenames stay narrow-alphabet (the directory
        # is scanned by storage tooling) while cross-instance collisions in
        # a shared workspace become impossible.
        self._instance_suffix = f"{os.getpid():x}{uuid.uuid4().hex[:6]}"

    @property
    def budget_per_message(self) -> int:
        """Char budget for a single message's tool results."""
        return self._budget

    @budget_per_message.setter
    def budget_per_message(self, value: int) -> None:
        self._budget = max(1, int(value))

    def check_and_replace(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Check tool results against budget, persist oversized ones.

        Two gates apply per tool step (its results separated only by their
        assistant_tool_call messages): any single result above
        ``persist_threshold`` is persisted immediately, and the step's
        cumulative content above ``budget_per_message`` is persisted
        largest-first until it fits.

        Returns:
            Tuple of (modified_messages, total_bytes_saved)
        """
        if self._results_dir.is_symlink():
            raise ValueError(
                "tool result persistence directory is a symbolic link"
            )
        if not self._results_dir.exists():
            self._results_dir.mkdir(parents=True, exist_ok=True)
        if self._results_dir.is_symlink():
            raise ValueError(
                "tool result persistence directory is a symbolic link"
            )
        self.maybe_cleanup_expired()

        modified = list(messages)
        bytes_saved = 0

        for i, msg in enumerate(modified):
            if msg.get("role") != "tool_result":
                continue
            if msg.get("toolName") == "load_skill" and not msg.get("isError"):
                # This is active instruction state, not disposable diagnostic
                # output. Full/Session compaction preserve its call/result
                # pair explicitly below.
                continue

            content = msg.get("content", "")
            content_size = len(content)

            if content_size <= self._persist_threshold:
                continue

            modified, bytes_saved = self._persist_message(
                modified, i, bytes_saved
            )

        # Per-step aggregate budget: N parallel results each under the single
        # threshold can still jointly exceed what one model request should
        # carry. Results belong to the same step when only their
        # assistant_tool_call messages separate them (one provider assistant
        # turn, identified by assistantTurnId). Persist the largest remaining
        # results of a step until the step fits.
        step_groups: dict[str, list[int]] = {}
        current_turn = ""
        for index, msg in enumerate(modified):
            if msg.get("role") == "assistant_tool_call":
                turn_id = str(msg.get("assistantTurnId", "") or "")
                if turn_id:
                    current_turn = turn_id
                continue
            if self._is_budget_eligible(msg):
                step_groups.setdefault(current_turn, []).append(index)

        for indices in step_groups.values():
            modified, bytes_saved = self._enforce_run_budget(
                modified, indices, bytes_saved
            )

        return modified, bytes_saved

    def _is_budget_eligible(self, msg: dict[str, Any]) -> bool:
        return msg.get("role") == "tool_result" and not (
            msg.get("toolName") == "load_skill" and not msg.get("isError")
        )

    def compact_read_history(
        self,
        messages: list[dict[str, Any]],
        *,
        eligible_ids: set[str],
        protected_ids: set[str],
        keep_recent: int = 2,
        history_budget: int = 16_000,
        source_paths: dict[str, str] | None = None,
        invalidated_ids: set[str] | None = None,
        max_anchor_paths: int = 2,
        max_anchor_chars: int = 8192,
    ) -> tuple[list[dict[str, Any]], int]:
        """Bound old inspection text after the model has seen it in full.

        Eligibility comes from completed, successful host-classified reads in
        this loop, never from model text. Preserve every call/result pair, all
        non-read authority documents and the entire just-completed batch (even
        if it contains more than ``keep_recent`` results). This is an in-memory
        projection: no cache writes are authorized by a read-only tool, and
        workflow workers cannot access the normal tool-results archive.

        The two latest successful inspection results remain full, in addition
        to every result in the current batch. A small complete read window
        from each of at most two recent source
        paths gets priority *inside* the ordinary history budget, not an
        additional allowance. Paths come only from host-observed successful
        read_file calls; neither their contents nor this retention policy
        confer write authority. A later successful read replaces a path's
        candidate even when it is too large to retain. Actual writes invalidate
        prior read IDs; no disk reads or permission checks occur here.
        As before, recent/current-batch results, small ordinary results and
        per-call shortened previews are outside a hard total-context bound.
        """
        from minicode.coding_progress import SOURCE_READ_TOOLS

        indices = [
            i for i, msg in enumerate(messages)
            if msg.get("role") == "tool_result"
            and msg.get("toolUseId") in eligible_ids
            and msg.get("toolName") in SOURCE_READ_TOOLS
            and msg.get("isError") is False
            and "_persisted_path" not in msg
            and not msg.get("_read_history_compacted")
        ]
        recent = set(indices[-max(1, keep_recent):])
        invalidated = invalidated_ids or set()
        latest_source: dict[str, int] = {}
        for i, msg in enumerate(messages):
            identity = msg.get("toolUseId")
            raw_path = (source_paths or {}).get(identity)
            if (
                msg.get("role") != "tool_result"
                or msg.get("toolName") != "read_file"
                or identity not in eligible_ids
                or identity in invalidated
                or msg.get("isError") is not False
                or str(msg.get("content", "")).startswith("[Read deduplicated:")
                or not isinstance(raw_path, str)
                or not raw_path
                or "\x00" in raw_path
            ):
                continue
            # Lexical identity only: resolve()/stat() would add unauthorized
            # filesystem observation and could follow a changed symlink.
            path = Path(os.path.normcase(os.path.abspath(self._workspace / raw_path)))
            relative_parts = Path(os.path.relpath(path, self._workspace)).parts
            if path.suffix.lower() not in _SOURCE_ANCHOR_SUFFIXES or any(
                part.lower() in _NON_PRODUCTION_ANCHOR_PARTS for part in relative_parts
            ):
                continue
            latest_source[str(path)] = i
        anchors: set[int] = set()
        anchor_size = 0
        for index in sorted(latest_source.values(), reverse=True):
            msg = messages[index]
            size = len(str(msg.get("content", "")))
            if (
                len(anchors) >= max(0, max_anchor_paths)
                or index not in indices
                or size > max_anchor_chars
                or size == 0
                or anchor_size + size > max(0, history_budget)
            ):
                continue
            anchors.add(index)
            anchor_size += size
        candidates = [
            i for i in indices if i not in recent
            and messages[i].get("toolUseId") not in protected_ids
        ]
        total = sum(len(str(messages[i].get("content", ""))) for i in candidates)
        if total <= history_budget:
            return list(messages), 0
        modified = list(messages)
        saved = 0
        for index in candidates:
            if total <= history_budget:
                break
            if index in anchors:
                continue
            msg = modified[index]
            content = str(msg.get("content", ""))
            old_size = len(content)
            if old_size <= 1000:
                continue
            preview = (
                f"[Earlier inspection output shortened: {old_size} characters. "
                "Original tool arguments are retained. If exact text is needed, "
                "read the relevant authorized source again; current contents "
                "may differ from this earlier observation.]\n"
                + content[:450] + "\n... [historical body omitted] ...\n" + content[-150:]
            )
            modified[index] = {**msg, "content": preview, "_read_history_compacted": True}
            saved += old_size - len(preview)
            total -= old_size - len(preview)
        return modified, saved

    def _enforce_run_budget(
        self,
        modified: list[dict[str, Any]],
        indices: list[int],
        bytes_saved: int,
    ) -> tuple[list[dict[str, Any]], int]:
        eligible = [
            index
            for index in indices
            if "_persisted_path" not in modified[index]
        ]
        if len(eligible) <= self.PROTECTED_RECENT_RESULTS:
            return modified, bytes_saved
        candidates = eligible[: -self.PROTECTED_RECENT_RESULTS]
        total = sum(
            len(str(modified[index].get("content", "")))
            for index in candidates
        )
        while total > self._budget and candidates:
            largest = max(
                candidates,
                key=lambda index: len(
                    str(modified[index].get("content", ""))
                ),
            )
            content_size = len(str(modified[largest].get("content", "")))
            modified, bytes_saved = self._persist_message(
                modified, largest, bytes_saved
            )
            preview_size = len(str(modified[largest].get("content", "")))
            total -= content_size - preview_size
            candidates.remove(largest)
        return modified, bytes_saved

    def _persist_message(
        self,
        modified: list[dict[str, Any]],
        index: int,
        bytes_saved: int,
    ) -> tuple[list[dict[str, Any]], int]:
        msg = modified[index]
        content = msg.get("content", "")
        content_size = len(content)
        tool_name = msg.get("toolName", "unknown")
        persisted = self._persist_content(content, tool_name, index)

        preview = self._generate_preview(content, tool_name, persisted.persisted_path)
        modified[index] = {
            **msg,
            "content": preview,
            "_persisted_path": str(persisted.persisted_path),
        }
        # Key by toolUseId: message indexes drift whenever compaction
        # removes messages, and an index-keyed cache then accumulates
        # orphan files for results that no longer exist.
        cache_key = str(msg.get("toolUseId") or f"{index}-{tool_name}")
        self._persisted[cache_key] = persisted
        return modified, bytes_saved + content_size - len(preview)

    def _persist_content(
        self, content: str, tool_name: str, index: int
    ) -> ToolResultPersisted:
        """Persist content to disk atomically."""
        safe_tool_name = _safe_tool_result_filename(tool_name)
        # Concurrent sub-agents share one workspace results directory; the
        # per-instance suffix keeps two agents persisting the same tool name
        # and message index in the same millisecond from overwriting each
        # other's file (os.replace would silently drop one).
        safe_name = (
            f"{safe_tool_name}_{index}_{int(time.time() * 1000)}"
            f"_{self._instance_suffix}.txt"
        )
        path = self._results_dir / safe_name
        if path.parent != self._results_dir:
            raise ValueError("persisted tool result escaped its directory")

        meta = {
            "tool_name": tool_name,
            "message_index": index,
            "original_size": len(content),
            "timestamp": time.time(),
        }
        header = json.dumps(meta, ensure_ascii=False) + "\n---CONTENT---\n"

        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self._results_dir), prefix=".tool_result_", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(header)
                f.write(content)
            os.replace(tmp_path, str(path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return ToolResultPersisted(
            original_size=len(content),
            persisted_path=path,
            preview_text="",
            tool_name=tool_name,
        )

    def _generate_preview(
        self, content: str, tool_name: str, path: Path
    ) -> str:
        """Generate preview text for persisted content."""
        lines = content.splitlines()
        head_lines = lines[:8]
        tail_lines = lines[-3:] if len(lines) > 12 else []

        parts = [
            f"[Tool result persisted to disk — {len(content)} chars]",
            f"Tool: {tool_name}",
            f"Path: {path}",
            "Use read_file with this path if the full result is needed.",
            "",
            "--- Preview (first/last lines) ---",
        ]
        parts.extend(head_lines)
        if tail_lines:
            parts.append(f"... ({len(lines) - len(head_lines) - len(tail_lines)} lines omitted) ...")
            parts.extend(tail_lines)

        preview = "\n".join(parts)
        return preview[:self.PREVIEW_MAX_CHARS]

    def get_persisted_count(self) -> int:
        return len(self._persisted)

    def get_total_saved_bytes(self) -> int:
        return sum(r.original_size for r in self._persisted.values())

    def maybe_cleanup_expired(self, now: float | None = None) -> None:
        """Run the TTL sweep at most once per cleanup interval per instance.

        Public so end-of-turn/session hooks can trigger the sweep even when
        no compaction pass ran (long idle sessions otherwise never prune).
        """
        current = time.time() if now is None else now
        if current - self._last_cleanup_at < self._CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup_at = current
        self.cleanup_expired(self.DEFAULT_MAX_AGE_SECONDS, now=current)

    def cleanup_expired(
        self,
        max_age_seconds: float,
        *,
        now: float | None = None,
    ) -> int:
        """Delete persisted result files older than ``max_age_seconds``.

        Only regular files directly inside the results directory are
        removed; symlinks and subdirectories are left untouched. In-memory
        entries whose backing file is gone are dropped so stats stay honest.
        """
        if max_age_seconds < 0 or self._results_dir.is_symlink():
            return 0
        current = time.time() if now is None else now
        removed = 0
        try:
            entries = list(self._results_dir.iterdir())
        except OSError:
            return 0
        for path in entries:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                if current - path.stat().st_mtime < max_age_seconds:
                    continue
                path.unlink()
                removed += 1
            except OSError:
                continue
        if removed:
            self._persisted = {
                key: record
                for key, record in self._persisted.items()
                if record.persisted_path.exists()
            }
        return removed


# ---------------------------------------------------------------------------
# Phase 3: Read Deduplication
# ---------------------------------------------------------------------------


class ReadDedupManager:
    """Hash-based file read deduplication.

    When the same file (same path + same content hash) is read again,
    returns a stub instead of re-injecting full content into context.
    """

    def __init__(self):
        self._entries: dict[str, ReadDedupEntry] = {}  # file_path -> entry
        self._stub_template = (
            "File unchanged since last read. "
            "The content from the earlier Read tool_result "
            "in this conversation is still current — refer to that instead."
        )

    def register_read(
        self, file_path: str, content: str, message_index: int
    ) -> bool:
        """Register a file read. Returns True if this is a new/different read."""
        content_hash = hashlib.md5(content.encode("utf-8"), usedforsecurity=False).hexdigest()

        existing = self._entries.get(file_path)
        if existing and existing.content_hash == content_hash:
            return False  # Duplicate

        self._entries[file_path] = ReadDedupEntry(
            file_path=file_path,
            content_hash=content_hash,
            timestamp=time.time(),
            message_index=message_index,
        )
        return True  # New or changed

    def should_dedup(self, file_path: str, content: str) -> bool:
        """Check if this read can be deduplicated."""
        content_hash = hashlib.md5(content.encode("utf-8"), usedforsecurity=False).hexdigest()
        existing = self._entries.get(file_path)
        return existing is not None and existing.content_hash == content_hash

    def get_stub(self, file_path: str) -> str:
        """Get dedup stub for a previously-read file."""
        entry = self._entries.get(file_path)
        if not entry:
            return ""
        return (
            f"[Read deduplicated: {file_path}]\n"
            f"{self._stub_template}\n"
            f"(Original content at message index {entry.message_index})"
        )

    def invalidate(self, file_path: str) -> None:
        """Invalidate cache for a specific file (e.g., after write)."""
        self._entries.pop(file_path, None)

    def reconcile(self, messages: list[dict[str, Any]]) -> int:
        """Drop entries whose referenced full read no longer exists.

        A dedup stub is only safe while its exact source tool result remains
        addressable in the live prompt. Compaction can replace, summarize, or
        reindex that message, so index and content hash are both revalidated.
        """
        invalid: list[str] = []
        for file_path, entry in self._entries.items():
            index = entry.message_index
            if not isinstance(index, int) or not 0 <= index < len(messages):
                invalid.append(file_path)
                continue
            message = messages[index]
            content = message.get("content")
            if (
                message.get("role") != "tool_result"
                or message.get("toolName") != "read_file"
                or not isinstance(content, str)
            ):
                invalid.append(file_path)
                continue
            live_hash = hashlib.md5(
                content.encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()
            if live_hash != entry.content_hash:
                invalid.append(file_path)
        for file_path in invalid:
            self._entries.pop(file_path, None)
        return len(invalid)

    def clear(self) -> None:
        self._entries.clear()


# ---------------------------------------------------------------------------
# Phase 4: Time-based Microcompact
# ---------------------------------------------------------------------------


class MicrocompactEngine:
    """Lightweight pre-compact optimization.

    Clears old tool results when they're unlikely to be in prompt cache
    anymore (time-based), reducing rewrite cost on next API call.
    """

    def __init__(self, config: MicrocompactState | None = None):
        self._state = config or MicrocompactState()

    def run_time_based_microcompact(
        self,
        messages: list[dict[str, Any]],
        now: float | None = None,
    ) -> CompactionResult:
        """Clear old tool results based on time since last assistant response.

        Does NOT generate summaries. Simply replaces old tool_result
        content with a fixed marker text.
        """
        now = now or time.time()
        elapsed = now - self._state.last_time_based_compact

        if elapsed < self._state.time_based_interval:
            return CompactionResult(
                success=False,
                strategy=CompactStrategy.MICROCOMPACT,
                trigger=CompactTrigger.MICROCOMPACT_TIME,
                messages=messages,
            )

        tool_results = [
            (i, m) for i, m in enumerate(messages)
            if m.get("role") == "tool_result"
            and m.get("toolName") != "load_skill"
            and not m.get("content", "").startswith("[Tool result persisted")
            and not m.get("content", "").startswith("[Old tool result")
        ]

        if len(tool_results) <= self._state.keep_recent_tool_results:
            return CompactionResult(
                success=False,
                strategy=CompactStrategy.MICROCOMPACT,
                trigger=CompactTrigger.MICROCOMPACT_TIME,
                messages=messages,
            )

        modified = list(messages)
        cleared_count = 0
        tokens_cleared = 0

        # Keep recent N, clear older ones
        keep_indices = {idx for idx, _ in tool_results[-self._state.keep_recent_tool_results:]}

        for idx, msg in tool_results:
            if idx in keep_indices:
                continue

            old_content = msg.get("content", "")
            old_size = len(old_content)
            modified[idx] = {
                **msg,
                "content": "[Old tool result content cleared by time-based microcompact]",
                "_microcompacted": True,
            }
            cleared_count += 1
            tokens_cleared += old_size // 4  # Rough token estimate

        self._state.last_time_based_compact = now
        self._state.total_tokens_cleared += tokens_cleared

        logger.info(
            "Time-based microcompact: cleared %d old tool results (~%d tokens)",
            cleared_count,
            tokens_cleared,
        )

        return CompactionResult(
            success=True,
            strategy=CompactStrategy.MICROCOMPACT,
            trigger=CompactTrigger.MICROCOMPACT_TIME,
            messages=modified,
            tokens_freed=tokens_cleared,
        )


# ---------------------------------------------------------------------------
# Phase 5: Session Memory Compact
# ---------------------------------------------------------------------------


def _tool_call_ids(message: dict[str, Any]) -> set[str]:
    """Tool-use IDs carried by a message (current or legacy block format)."""
    if message.get("role") == "assistant_tool_call":
        call_id = str(message.get("toolUseId", "") or "")
        return {call_id} if call_id else set()
    if message.get("role") == "assistant" and isinstance(message.get("content"), list):
        ids: set[str] = set()
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                call_id = str(block.get("id", "") or "")
                if call_id:
                    ids.add(call_id)
        return ids
    return set()


def _adjust_tail_cut_for_tool_pairs(
    messages: list[dict[str, Any]], cut: int
) -> int:
    """Move a tail cut point so no tool_call/tool_result pair is split.

    Messages before ``cut`` are dropped; the tail from ``cut`` onward is
    kept. The API contract requires every tool_result to reference a
    tool_use present in the same request, so a kept result whose call was
    dropped invalidates the request. A provider assistant turn may also own
    multiple tool calls, so retaining one member identified by
    ``assistantTurnId`` retains that whole turn. Walk the cut back to a fixed
    point satisfying both contracts.
    """
    cut = max(0, min(cut, len(messages)))
    while True:
        kept_result_ids = {
            str(m.get("toolUseId", "") or "")
            for m in messages[cut:]
            if m.get("role") == "tool_result"
        }
        kept_result_ids.discard("")
        new_cut = cut
        if kept_result_ids:
            for index in range(cut - 1, -1, -1):
                if _tool_call_ids(messages[index]) & kept_result_ids:
                    new_cut = index
                    break

        kept_turn_ids = {
            str(message.get("assistantTurnId", "") or "")
            for message in messages[new_cut:]
            if message.get("role") == "assistant_tool_call"
        }
        kept_turn_ids.discard("")
        if kept_turn_ids:
            for index, message in enumerate(messages[:new_cut]):
                if (
                    message.get("role") == "assistant_tool_call"
                    and str(message.get("assistantTurnId", "") or "")
                    in kept_turn_ids
                ):
                    new_cut = min(new_cut, index)
        if new_cut == cut:
            return cut
        cut = new_cut


def _loaded_skill_context_indices(
    messages: list[dict[str, Any]],
) -> set[int]:
    """Successful load_skill call/result pairs that remain active authority."""
    indices: set[int] = set()
    for result_index, message in enumerate(messages):
        if (
            message.get("role") != "tool_result"
            or message.get("toolName") != "load_skill"
            or message.get("isError")
        ):
            continue
        tool_use_id = str(message.get("toolUseId", "") or "")
        if not tool_use_id:
            continue
        call_index = next(
            (
                index
                for index in range(result_index - 1, -1, -1)
                if tool_use_id in _tool_call_ids(messages[index])
            ),
            None,
        )
        if call_index is not None:
            indices.update({call_index, result_index})
    turn_ids = {
        str(messages[index].get("assistantTurnId", "") or "")
        for index in indices
        if messages[index].get("role") == "assistant_tool_call"
    }
    turn_ids.discard("")
    if not turn_ids:
        return indices

    grouped_call_ids: set[str] = set()
    for index, message in enumerate(messages):
        if (
            message.get("role") == "assistant_tool_call"
            and str(message.get("assistantTurnId", "") or "") in turn_ids
        ):
            indices.add(index)
            call_id = str(message.get("toolUseId", "") or "")
            if call_id:
                grouped_call_ids.add(call_id)
    for index, message in enumerate(messages):
        if (
            message.get("role") == "tool_result"
            and str(message.get("toolUseId", "") or "") in grouped_call_ids
        ):
            indices.add(index)
    return indices


class SessionMemoryCompactEngine:
    """Uses existing MemoryManager entries as compaction summary base.

    Instead of calling the model to generate a summary, this leverages
    already-maintained memory entries (project decisions, conventions,
    patterns) to form the compact summary, preserving recent messages
    verbatim as a tail.
    """

    TAIL_MIN_TOKENS = 10000
    TAIL_MIN_MESSAGES = 5
    TAIL_MAX_TOKENS = 40000

    def __init__(self, memory_manager=None):
        self._memory = memory_manager

    def try_session_memory_compact(
        self,
        messages: list[dict[str, Any]],
        context_window: int,
        estimate_fn=None,
        config: AutoCompactConfig | None = None,
        query: str | None = None,
        transcript_summarizer: Any = None,
    ) -> CompactionResult | None:
        """Attempt session memory compact. Returns None if not applicable."""

        config = config or AutoCompactConfig()

        if not config.session_memory_enabled:
            return None

        if self._memory is None:
            return None

        revalidate_prompt = getattr(
            self._memory,
            "revalidate_prompt_messages",
            None,
        )
        if callable(revalidate_prompt):
            messages = revalidate_prompt(list(messages))

        retrieval_query = (query or "").strip()
        if not retrieval_query:
            retrieval_query = next(
                (
                    str(message.get("content", "")).strip()
                    for message in reversed(messages)
                    if message.get("role") == "user"
                    and str(message.get("content", "")).strip()
                ),
                "",
            )
        if not retrieval_query:
            return None

        # Persistent memory is only a summary base when it matches the latest
        # effective user request; queryless all-active injection is forbidden.
        # The canonical Pipeline returns exact IDs alongside the rendered
        # text. Compatibility managers can still supply text, but cannot claim
        # injection attribution without a structured receipt.
        memory_receipt: MemoryPromptReceipt | None = None
        reused_memory_ids: tuple[str, ...] = ()
        prepare_context = getattr(
            self._memory,
            "prepare_compaction_context",
            None,
        )
        if callable(prepare_context):
            materialized_fragments = verified_materialized_memory_fragments(
                messages
            )
            already_materialized_proofs = (
                tuple(
                    (entry_id, digest)
                    for entry_id, digest, _fragment in materialized_fragments
                )
            )
            prepared_receipt = prepare_context(
                retrieval_query,
                max_total_tokens=6000,
                already_materialized_proofs=already_materialized_proofs,
            )
            reused_memory_ids = tuple(
                getattr(prepared_receipt, "reused_ids", ()) or ()
            )
            memory_context = prepared_receipt.prompt_text
            if memory_context and prepared_receipt.rendered_ids:
                memory_receipt = prepared_receipt
            current_proof_by_id = dict(
                getattr(prepared_receipt, "rendered_proofs", ()) or ()
            )
            stale_fragments = tuple(
                (entry_id, digest, fragment)
                for entry_id, digest, fragment in materialized_fragments
                if entry_id in current_proof_by_id
                and current_proof_by_id[entry_id] != digest
            )
            if stale_fragments:
                stale_pairs = {
                    (entry_id, digest)
                    for entry_id, digest, _fragment in stale_fragments
                }
                stale_ids = {entry_id for entry_id, _digest in stale_pairs}
                sanitized_messages: list[dict[str, Any]] = []
                for message in messages:
                    if message.get("role") != "system":
                        sanitized_messages.append(message)
                        continue
                    sanitized = dict(message)
                    visible_text = str(sanitized.get("content", ""))
                    for _entry_id, _digest, fragment in stale_fragments:
                        visible_text = visible_text.replace(fragment, "")
                    sanitized["content"] = visible_text
                    raw_proofs = sanitized.get("_memory_rendered_proofs", ())
                    if isinstance(raw_proofs, (list, tuple)):
                        sanitized["_memory_rendered_proofs"] = tuple(
                            tuple(raw_proof)
                            for raw_proof in raw_proofs
                            if isinstance(raw_proof, (list, tuple))
                            and len(raw_proof) == 3
                            and (
                                str(raw_proof[0]),
                                str(raw_proof[1]),
                            )
                            not in stale_pairs
                        )
                    raw_ids = sanitized.get("_memory_rendered_ids", ())
                    if isinstance(raw_ids, (list, tuple)):
                        sanitized["_memory_rendered_ids"] = tuple(
                            entry_id
                            for entry_id in raw_ids
                            if isinstance(entry_id, str)
                            and entry_id not in stale_ids
                        )
                    sanitized_messages.append(sanitized)
                messages = sanitized_messages
        else:
            memory_context = self._memory.get_relevant_context(
                query=retrieval_query,
                max_tokens=6000,
            )
        if not memory_context.strip() and not reused_memory_ids:
            return None  # No memory available, fall back to Full Compact

        # Find where to cut: keep recent tail.  The newest previous boundary
        # is not retained verbatim, but its summary must participate in the
        # next summary; otherwise a second compact permanently forgets every
        # decision that survived only through the first one.
        previous_summary = _previous_compaction_summary(messages)
        system_msgs = _strip_old_boundaries(
            [m for m in messages if m.get("role") == "system"]
        )
        non_system = [m for m in messages if m.get("role") != "system"]

        # Calculate tail from the end
        tail_tokens = 0
        # Default to "keep everything": if the whole conversation fits in the
        # expansion budget the loop never breaks, and a default of len()
        # would delete the entire conversation except system messages.
        tail_start = 0
        estimate = estimate_fn or (lambda m: len(str(m)) // 4)

        for i in range(len(non_system) - 1, -1, -1):
            msg_tokens = estimate(non_system[i])
            if tail_tokens + msg_tokens > config.max_expand_tokens and \
               (len(non_system) - i) >= config.min_keep_messages:
                tail_start = i + 1
                break
            tail_tokens += msg_tokens

        if tail_tokens < self.TAIL_MIN_TOKENS:
            tail_start = max(0, len(non_system) - config.min_keep_messages)

        # Ensure we don't cut tool_use/tool_result pairs
        tail_start = self._adjust_for_tool_pair(non_system, tail_start)

        skill_indices = {
            index
            for index in _loaded_skill_context_indices(non_system)
            if index < tail_start
        }
        dropped = [
            message
            for index, message in enumerate(non_system[:tail_start])
            if index not in skill_indices
        ]
        if not dropped or transcript_summarizer is None:
            # Durable project memory is not an episodic summary of the current
            # turn. Without a summary of the exact dropped transcript this
            # strategy must decline and let Full Compact handle it.
            return None
        try:
            summary_input = (
                [previous_summary, *dropped]
                if previous_summary is not None
                else dropped
            )
            transcript_summary = transcript_summarizer.summarize(summary_input)
        except (TurnCancellationRequested, ModelCallDeadlineExceeded):
            raise
        except Exception as exc:  # noqa: BLE001 - dispatcher falls back safely
            logger.warning("Session transcript summary failed (%s)", exc)
            return None
        if not transcript_summary:
            return None
        transcript_summary = _merge_summary_with_critical_anchors(
            transcript_summary,
            summary_input,
        )

        # Build compacted messages
        boundary = CompactBoundary(
            trigger=CompactTrigger.AUTO,
            strategy=CompactStrategy.SESSION_MEMORY,
            tokens_before=sum(estimate(m) for m in messages),
        )

        compacted = []
        compacted.append({
            "role": "system",
            "content": (
                f"[Context compacted at {time.strftime('%H:%M:%S')} via Session Memory]\n"
                f"Messages removed: {tail_start}. Tokens before: ~{boundary.tokens_before}\n\n"
                f"## Dropped Conversation Summary\n\n{transcript_summary}"
                "\n\n"
                "--- Recent conversation continues below ---"
            ),
            "_compact_boundary": True,
            "_session_memory_boundary_schema": 2,
        })
        if memory_context:
            memory_message = {
                "role": "system",
                "content": f"## Relevant Durable Memory\n\n{memory_context}",
                # Current-Turn prompt material, never historical conversation.
                "_session_memory_context": True,
            }
            if memory_receipt is not None:
                memory_message["_memory_rendered_ids"] = tuple(
                    memory_receipt.rendered_ids
                )
                memory_message["_memory_rendered_proofs"] = (
                    verified_receipt_memory_fragments(memory_receipt)
                )
                memory_message["_memory_authority_revision"] = (
                    memory_receipt.authority_revision
                )
                block_proof = memory_prompt_block_proof(
                    str(memory_message["content"])
                )
                if block_proof[0]:
                    memory_message["_memory_prompt_blocks"] = (block_proof,)
                # Structural single-instance guarantee: an earlier
                # ``_session_memory_context`` block may be dropped only when
                # every entry it carries is re-rendered verbatim in the new
                # block. Reused entries keep their text exclusively in the
                # earlier block (``prepare_compaction_context`` suppresses
                # reused text from the new prompt), so those blocks must stay.
                fresh_ids = frozenset(memory_receipt.rendered_ids)

                def _fully_rerendered(message: dict[str, Any]) -> bool:
                    proofs = message.get("_memory_rendered_proofs") or ()
                    entry_ids = {
                        str(proof[0])
                        for proof in proofs
                        if isinstance(proof, (list, tuple))
                        and len(proof) == 3
                        and str(proof[0])
                    }
                    return bool(entry_ids) and entry_ids <= fresh_ids

                system_msgs = [
                    message
                    for message in system_msgs
                    if not (
                        message.get("_session_memory_context")
                        and _fully_rerendered(message)
                    )
                ]
            compacted.append(memory_message)

        # Add preserved tail
        tail = [non_system[index] for index in sorted(skill_indices)]
        tail.extend(non_system[tail_start:])
        compacted.extend(tail)

        # Re-add system messages at front
        final = system_msgs + compacted

        boundary.tokens_after = sum(estimate(m) for m in final)
        boundary.messages_removed = len(messages) - len(final)
        boundary.preserved_segment = (
            len(system_msgs) + 1 + (1 if memory_context else 0),
            len(final) - 1,
        )

        # Check if compaction actually helped
        if boundary.tokens_after >= boundary.tokens_before * 0.95:
            return None  # Not enough savings

        logger.info(
            "Session Memory Compact: %d → %d tokens (%d freed)",
            boundary.tokens_before,
            boundary.tokens_after,
            boundary.tokens_before - boundary.tokens_after,
        )

        return CompactionResult(
            success=True,
            strategy=CompactStrategy.SESSION_MEMORY,
            trigger=CompactTrigger.AUTO,
            messages=final,
            boundary=boundary,
            tokens_freed=boundary.tokens_before - boundary.tokens_after,
            summary_text=(
                f"## Dropped Conversation Summary\n{transcript_summary}"
                + (
                    f"\n\n## Relevant Durable Memory\n{memory_context}"
                    if memory_context
                    else ""
                )
            ),
            memory_receipt=memory_receipt,
            materialization_state=(
                CompactionMaterializationState.PENDING
                if memory_receipt is not None
                else CompactionMaterializationState.NOT_REQUIRED
            ),
        )

    @staticmethod
    def _adjust_for_tool_pair(messages: list[dict], cut_point: int) -> int:
        """Adjust cut point to avoid breaking tool_use/tool_result pairs."""
        return _adjust_tail_cut_for_tool_pairs(messages, cut_point)


_PREVIOUS_COMPACTION_SUMMARY_MAX_CHARS = 12_000


def _is_compaction_boundary(message: dict[str, Any]) -> bool:
    return bool(
        message.get("role") == "system"
        and (
            message.get("_compact_boundary")
            or message.get("_reactive_compact")
        )
    )


_SESSION_MEMORY_SECTION_HEADER = "## Relevant Durable Memory\n\n"
_SESSION_MEMORY_CONTINUATION = (
    "\n\n--- Recent conversation continues below ---"
)
_SESSION_MEMORY_BOUNDARY_SCHEMA = 2


def _clear_boundary_memory_metadata(
    message: dict[str, Any],
) -> dict[str, Any]:
    sanitized = dict(message)
    for field_name in (
        "_memory_authority_revision",
        "_memory_prompt_blocks",
        "_memory_rendered_ids",
        "_memory_rendered_proofs",
        "_session_memory_context",
    ):
        sanitized.pop(field_name, None)
    return sanitized


def _strip_legacy_session_memory_boundary(
    message: dict[str, Any],
) -> dict[str, Any] | None:
    """Remove the old mixed Memory section while preserving its summary."""
    if not _is_compaction_boundary(message):
        return dict(message)
    if (
        message.get("_session_memory_boundary_schema")
        == _SESSION_MEMORY_BOUNDARY_SCHEMA
    ):
        return dict(message)

    content = str(message.get("content", ""))
    first_line = content.splitlines()[0] if content else ""
    header_index = content.find(_SESSION_MEMORY_SECTION_HEADER)
    if header_index < 0:
        # Session Memory boundaries produced before schema v2 mixed authority
        # into one system message. If their structure cannot be proved, drop
        # the boundary rather than re-authorize unknown historical text.
        if "via Session Memory" in first_line:
            return None
        return dict(message)
    trailer_index = content.rfind(_SESSION_MEMORY_CONTINUATION)
    if trailer_index < header_index:
        return None
    removal_start = header_index
    while removal_start > 0 and content[removal_start - 1] == "\n":
        removal_start -= 1
    sanitized = _clear_boundary_memory_metadata(message)
    sanitized["content"] = (
        content[:removal_start].rstrip()
        + content[trailer_index:]
    )
    return sanitized


def strip_historical_session_memory(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove current-Turn Memory material before history is reused.

    The conversation summary remains available, but Memory must be selected
    again for the new user request by canonical retrieval. The transformation
    is intentionally pure and idempotent so every entrypoint can share it.
    """
    sanitized_messages: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if (
            message.get("role") == "system"
            and message.get("_session_memory_context")
        ):
            continue
        sanitized = _strip_legacy_session_memory_boundary(message)
        if sanitized is not None:
            sanitized_messages.append(sanitized)
    return sanitized_messages


def _previous_compaction_summary(
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Project only the newest prior boundary into the next summary input.

    Historical boundary messages themselves are still replaced on every
    cycle, so the live prompt stays bounded.  A synthetic assistant message
    gives the summarizer continuity without granting the old boundary a
    second system-message authority surface.
    """
    for message in reversed(messages):
        if not _is_compaction_boundary(message):
            continue
        sanitized = strip_historical_session_memory([message])
        if not sanitized:
            return None
        content = str(sanitized[0].get("content", "")).strip()
        if not content:
            return None
        if len(content) > _PREVIOUS_COMPACTION_SUMMARY_MAX_CHARS:
            half = (_PREVIOUS_COMPACTION_SUMMARY_MAX_CHARS - 80) // 2
            content = (
                content[:half]
                + "\n... [previous compact summary bounded] ...\n"
                + content[-half:]
            )
        return {
            "role": "assistant",
            "content": "[Previous compacted context]\n" + content,
            "_previous_compact_summary": True,
        }
    return None


def _strip_old_boundaries(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop boundary markers from previous compactions.

    Each compact inserts a ``role=system`` marker describing what happened.
    Keeping every historical marker made the system prompt grow monotonically
    — the opposite of compaction. The newest summary (added by the caller
    after this strip) is the only one that stays.
    """
    return [
        message
        for message in messages
        if not _is_compaction_boundary(message)
    ]


_CRITICAL_STATE_CUE_RE = re.compile(
    r"(?i)(?:\b(?:goal|objective|constraint|requirement|must|never|accepted|"
    r"decision|rejected|reject|verified|verification|passed|failed|remaining|"
    r"root\s+cause|do\s+not|avoid)\b|目标|约束|要求|必须|不得|决策|拒绝|验证|"
    r"通过|失败|剩余|根因)"
)
_CRITICAL_STATE_IDENTIFIER_RE = re.compile(
    r"\b[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)+\b"
)
_CRITICAL_STATE_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")
_CRITICAL_STATE_HEADER = "## Deterministic Critical-State Anchors"


def _critical_state_anchors(
    messages: list[dict[str, Any]],
    *,
    max_items: int = 24,
    max_chars: int = 4_000,
) -> list[str]:
    """Extract bounded lexical anchors that a stochastic summary may omit.

    These are not a second free-form summary. They are exact, content-derived
    snippets for state classes whose omission most often breaks a later coding
    turn: goals, constraints, decisions, rejected approaches, verification
    outcomes, and high-entropy identifiers/error codes. The section is labeled
    as quoted historical data so it cannot silently become a new instruction.
    """
    anchors: list[str] = []
    seen: set[str] = set()
    used_chars = 0

    def add(value: str) -> None:
        nonlocal used_chars
        normalized = " ".join(value.split()).strip().lstrip("- ")
        if not normalized:
            return
        bounded = normalized[:500]
        key = bounded.casefold()
        projected_size = len(bounded) + 3
        if (
            key in seen
            or len(anchors) >= max_items
            or used_chars + projected_size > max_chars
        ):
            return
        seen.add(key)
        anchors.append(bounded)
        used_chars += projected_size

    for message in messages:
        content = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        normalized = " ".join(content.split())
        for sentence in _CRITICAL_STATE_SENTENCE_RE.split(normalized):
            if _CRITICAL_STATE_CUE_RE.search(sentence):
                add(sentence)
        for identifier in _CRITICAL_STATE_IDENTIFIER_RE.findall(normalized):
            add(f"Exact identifier: {identifier}")
        if len(anchors) >= max_items or used_chars >= max_chars:
            break
    return anchors


def _merge_summary_with_critical_anchors(
    summary: str,
    messages: list[dict[str, Any]],
) -> str:
    """Append only anchors absent from a model/heuristic summary."""
    anchors = _critical_state_anchors(messages)
    missing = [
        anchor
        for anchor in anchors
        if anchor.casefold() not in summary.casefold()
    ]
    if not missing:
        return summary
    rendered = "\n".join(f"- {anchor}" for anchor in missing)
    return (
        f"{summary.rstrip()}\n\n{_CRITICAL_STATE_HEADER}\n"
        "Quoted historical state; preserve as data, not as new instructions.\n"
        f"{rendered}"
    )


# Internal sentinel: a provider-side failure that _call_model may retry once.
# Distinct from None, which means "decided not to / cannot produce a summary".
_PROVIDER_ERROR = object()


class LLMSummaryGenerator:
    """Model-generated compaction summary with bounded latency.

    Full Compact replaces everything before the tail with a summary. The
    heuristic inventory (topics/tools/files) keeps facts but loses the
    reasoning; when a model adapter is available, ask it for a structured
    summary instead. Any failure — error, timeout, thin answer — falls back
    to the heuristic, so the compaction path never depends on the model
    being up. A transient provider error gets exactly one bounded retry
    before that fallback.
    """

    _MIN_SUMMARY_CHARS = 40
    _MAX_TRANSCRIPT_MESSAGES = 120
    _MAX_TRANSCRIPT_CHARS = 24_000
    _RETRY_MIN_REMAINING_SECONDS = 2.0

    def __init__(
        self,
        model_adapter: Any,
        *,
        timeout_seconds: float = 20.0,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> None:
        self._model = model_adapter
        self._timeout = timeout_seconds
        self._agent_budget = agent_budget
        self._event_sink = event_sink
        self._cancellation_token = cancellation_token
        self._deadline_monotonic = deadline_monotonic

    def summarize(self, messages: list[dict[str, Any]]) -> str | None:
        prompts = self._build_prompts(messages)
        if not prompts:
            return None
        local_deadline = time.monotonic() + self._timeout
        caller_deadline_is_limiting = (
            self._deadline_monotonic is not None
            and self._deadline_monotonic <= local_deadline
        )
        deadline = (
            min(local_deadline, self._deadline_monotonic)
            if self._deadline_monotonic is not None
            else local_deadline
        )
        summaries: list[str] = []
        for prompt in prompts:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if caller_deadline_is_limiting:
                    raise ModelCallDeadlineExceeded(
                        "agent deadline exceeded during context compaction"
                    )
                logger.warning(
                    "LLM summary timed out after %.1fs; using heuristic summary",
                    self._timeout,
                )
                return None
            summary = self._summarize_prompt(
                prompt,
                timeout=remaining,
                propagate_deadline=caller_deadline_is_limiting,
            )
            if summary is None:
                return None
            summaries.append(summary)

        if len(summaries) == 1:
            return summaries[0]
        # Keep every chunk summary. A later token-savings gate rejects the
        # compaction if this aggregate is too large; silently discarding a
        # middle chunk would be worse than declining to compact.
        return "\n\n".join(
            f"### Transcript chunk {index + 1}/{len(summaries)}\n{summary}"
            for index, summary in enumerate(summaries)
        )

    def _summarize_prompt(
        self,
        prompt: list[dict[str, Any]],
        *,
        timeout: float,
        propagate_deadline: bool,
    ) -> str | None:
        call_deadline = min(time.monotonic() + timeout, time.monotonic() + self._timeout)
        if self._deadline_monotonic is not None:
            call_deadline = min(call_deadline, self._deadline_monotonic)
        return self._call_model(
            prompt,
            self._agent_budget,
            deadline_monotonic=call_deadline,
            propagate_deadline=propagate_deadline,
        )

    @staticmethod
    def _message_line(message: dict[str, Any]) -> str:
        """Return a bounded textual representation of one dropped message."""
        role = str(message.get("role", "unknown") or "unknown")
        if role == "assistant_tool_call":
            tool_name = str(message.get("toolName", "?") or "?")
            raw_input = message.get("input", {})
            try:
                rendered_input = json.dumps(raw_input, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                rendered_input = str(raw_input)
            content = rendered_input[:2_000].replace("\n", " ").strip()
            return f"[tool {tool_name} call] {content}"
        content = str(message.get("content", ""))[:2_000].replace("\n", " ").strip()
        if role == "tool_result":
            return f"[tool {message.get('toolName', '?')} result] {content}"
        return f"{role}: {content}"

    def _build_prompts(self, messages: list[dict[str, Any]]) -> list[list[dict]]:
        """Build bounded prompts without leaving an unsummarized middle gap.

        Every dropped message contributes one line to exactly one chunk. The
        previous implementation stopped at the first 24k characters, while a
        separate recent tail kept the end, deterministically deleting the
        middle of long conversations.
        """
        chunks: list[list[str]] = []
        lines: list[str] = []
        total = 0
        for message in messages:
            line = self._message_line(message)
            if not line.strip():
                line = f"{message.get('role', 'unknown')}: [empty message]"
            line_size = len(line) + (1 if lines else 0)
            if lines and (
                total + line_size > self._MAX_TRANSCRIPT_CHARS
                or len(lines) >= self._MAX_TRANSCRIPT_MESSAGES
            ):
                chunks.append(lines)
                lines = []
                total = 0
                line_size = len(line)
            lines.append(line)
            total += line_size
        if lines:
            chunks.append(lines)

        instruction = (
            "Summarize the conversation above for a coding agent whose older "
            "context is being compacted. Write in the same language the user "
            "used. Keep it under 400 words and preserve, in this order:\n"
            "1. The user's task(s) and explicit constraints\n"
            "2. Key decisions made and why (including rejected alternatives)\n"
            "3. Verified facts: what was changed/tested and the outcome\n"
            "4. Current state and remaining work\n"
            "Copy exact identifiers, error codes, paths, version strings, and "
            "user-provided marker values verbatim. Never drop an explicit goal, "
            "constraint, decision, or rejected alternative merely to meet the "
            "word limit. Use short bullet points. Do not include pleasantries or "
            "meta-commentary about summarizing."
        )
        prompts: list[list[dict]] = []
        for index, chunk in enumerate(chunks):
            transcript = "\n".join(chunk)
            chunk_context = (
                f"This is transcript chunk {index + 1} of {len(chunks)}. "
                "Summarize only this chunk; every chunk will be retained.\n\n"
                if len(chunks) > 1
                else ""
            )
            prompts.append(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a precise context-compaction assistant for a "
                            "coding agent. You compress conversation history without "
                            "losing task-critical information."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"{chunk_context}{transcript}\n\n---\n\n{instruction}"
                        ),
                    },
                ]
            )
        return prompts

    def _call_model(
        self,
        prompt_messages: list[dict],
        agent_budget: Any,
        *,
        deadline_monotonic: float,
        propagate_deadline: bool,
    ) -> str | None:
        result = self._call_model_once(
            prompt_messages,
            agent_budget,
            deadline_monotonic=deadline_monotonic,
            propagate_deadline=propagate_deadline,
        )
        if result is not _PROVIDER_ERROR:
            return result
        # Bounded retry: a transient provider error (connection reset, 5xx,
        # incomplete read) should not immediately demote the compaction to
        # the heuristic inventory. Exactly one extra attempt, only while the
        # deadline still leaves room for it. Budget exhaustion, timeouts,
        # cancellation and turn-deadline propagation are never retried.
        if (
            deadline_monotonic - time.monotonic()
            < self._RETRY_MIN_REMAINING_SECONDS
        ):
            return None
        logger.info(
            "LLM summary provider error; one bounded retry (%.1fs left)",
            deadline_monotonic - time.monotonic(),
        )
        retried = self._call_model_once(
            prompt_messages,
            agent_budget,
            deadline_monotonic=deadline_monotonic,
            propagate_deadline=propagate_deadline,
        )
        if retried is _PROVIDER_ERROR:
            return None
        return retried

    def _call_model_once(
        self,
        prompt_messages: list[dict],
        agent_budget: Any,
        *,
        deadline_monotonic: float,
        propagate_deadline: bool,
    ) -> str | None | object:
        budget_reservation = None
        operation_id = new_model_operation_id()
        started_at = time.monotonic()
        if self._event_sink is not None:
            emit_event_safely(
                self._event_sink,
                "model.started",
                payload={
                    "operationId": operation_id,
                    "purpose": "context_compaction",
                },
            )
        try:
            if agent_budget is not None:
                budget_reservation = agent_budget.reserve_model_call(
                    estimate_budgeted_request_input(self._model, prompt_messages,
                        agent_budget=agent_budget,
                        estimated_input=self._estimate_prompt_tokens(prompt_messages)),
                    preserve_model_calls=1,
                )
            step = call_model_next(
                self._model,
                prompt_messages,
                cancellation_token=self._cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
        except AgentBudgetExceeded:
            logger.warning(
                "LLM summary skipped: shared Agent turn budget exhausted"
            )
            self._emit_failure(operation_id, started_at, "budget_exhausted")
            return None
        except TurnCancellationRequested:
            self._settle_failed_budget(agent_budget, budget_reservation)
            self._emit_failure(operation_id, started_at, "interrupted")
            raise
        except ModelCallDeadlineExceeded:
            self._settle_failed_budget(
                agent_budget,
                budget_reservation,
                charge_estimate=True,
            )
            self._emit_failure(operation_id, started_at, "timeout")
            if propagate_deadline:
                raise
            logger.warning(
                "LLM summary timed out after %.1fs; using heuristic summary",
                self._timeout,
            )
            return None
        except Exception as exc:  # noqa: BLE001 - fallback is the contract
            self._settle_failed_budget(agent_budget, budget_reservation)
            self._emit_failure(operation_id, started_at, "provider_error")
            logger.warning("LLM summary call failed (%s); heuristic fallback", exc)
            return _PROVIDER_ERROR

        usage = project_model_usage(getattr(step, "usage", None))
        try:
            cost_payload = project_model_cost_event(
                model=self._model,
                usage=usage,
                operation_id=operation_id,
            )
        except BaseException:  # noqa: BLE001 - observation stays optional
            cost_payload = pricing_failure_event_payload(operation_id)
        if agent_budget is not None:
            try:
                record_budgeted_model_call(
                    agent_budget,
                    model=self._model,
                    usage=usage,
                    reservation=budget_reservation,
                    cost_payload=cost_payload,
                )
            except Exception as exc:  # noqa: BLE001 - accounting is advisory
                logger.warning("LLM summary budget recording failed: %s", exc)
        duration_ms = self._duration_ms(started_at)
        completed_payload: dict[str, object] = {
            "operationId": operation_id,
            "purpose": "context_compaction",
            "resultType": "assistant",
            "contentPresent": bool(getattr(step, "content", "")),
            "toolCallCount": 0,
            "usage": usage,
        }
        if duration_ms is not None:
            completed_payload["durationMs"] = duration_ms
        emit_event_safely(
            self._event_sink,
            "model.completed",
            payload=completed_payload,
        )
        cost_payload["purpose"] = "context_compaction"
        emit_event_safely(
            self._event_sink,
            "model.costed",
            payload=cost_payload,
        )
        text = str(getattr(step, "content", "") or "").strip()
        if len(text) < self._MIN_SUMMARY_CHARS:
            return None
        return text[:8_000]

    @staticmethod
    def _settle_failed_budget(
        agent_budget: Any,
        reservation: Any,
        *,
        charge_estimate: bool = False,
    ) -> None:
        settle = getattr(agent_budget, "fail_model_call", None)
        if callable(settle):
            settle(reservation, charge_estimate=charge_estimate)

    @staticmethod
    def _duration_ms(started_at: float) -> int | None:
        try:
            return project_model_duration_ms(started_at, time.monotonic())
        except BaseException:  # noqa: BLE001 - observation stays optional
            return None

    def _emit_failure(
        self,
        operation_id: str,
        started_at: float,
        failure_kind: str,
    ) -> None:
        payload: dict[str, object] = {
            "operationId": operation_id,
            "purpose": "context_compaction",
            "failureKind": failure_kind,
        }
        duration_ms = self._duration_ms(started_at)
        if duration_ms is not None:
            payload["durationMs"] = duration_ms
        emit_event_safely(
            self._event_sink,
            "model.failed",
            payload=payload,
        )

    @staticmethod
    def _estimate_prompt_tokens(prompt_messages: list[dict]) -> int:
        try:
            from minicode.context_manager import estimate_message_tokens

            return sum(
                estimate_message_tokens(message) for message in prompt_messages
            )
        except Exception:  # noqa: BLE001 - estimate is advisory
            return 1_000


# ---------------------------------------------------------------------------
# Phase 6: Auto Compact High-Water Dispatcher
# ---------------------------------------------------------------------------


class AutoCompactDispatcher:
    """High-water mark auto-compaction dispatcher.

    Not a multi-level percentage selector. Instead:
    - Monitors token usage against threshold
    - Tries Session Memory Compact first
    - Falls back to Full Compact
    - Has circuit breaker for consecutive failures
    """

    def __init__(
        self,
        context_window: int = 200000,
        config: AutoCompactConfig | None = None,
        memory_manager=None,
        estimate_fn=None,
        summary_generator: Any = None,
    ):
        self._context_window = context_window
        self._config = config or AutoCompactConfig()
        self._memory = memory_manager
        self._estimate = estimate_fn or (lambda m: len(str(m)) // 4)
        self._summary_generator = summary_generator
        self._consecutive_failures = 0
        self._failed_state_digests: dict[CompactStrategy, str] = {}
        self._boundaries: list[CompactBoundary] = []
        self._materialization_outcomes: dict[
            str,
            CompactionMaterializationState,
        ] = {}
        self._materialization_outcome_limit = 1024
        self._suppressed_until: float = 0.0  # Warning suppression after compact
        self._session_memory_engine = SessionMemoryCompactEngine(memory_manager)

    @property
    def threshold_tokens(self) -> int:
        return int(self._context_window * self._config.threshold_ratio)

    @property
    def blocking_limit(self) -> int:
        return int(self._context_window * 0.97)

    @property
    def is_tripped(self) -> bool:
        return self._consecutive_failures >= self._config.circuit_breaker_limit

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @staticmethod
    def _message_state_digest(messages: list[dict[str, Any]]) -> str:
        """Identify one message state without retaining or exposing content."""
        digest = hashlib.sha256()
        for message in messages:
            try:
                encoded = json.dumps(
                    message,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=lambda value: {
                        "__non_json_type__": type(value).__name__,
                    },
                ).encode("utf-8", errors="replace")
            except (TypeError, ValueError, RecursionError):
                encoded = (
                    f"{type(message).__name__}:{len(str(message))}"
                ).encode("utf-8", errors="replace")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        return digest.hexdigest()

    def _blocked_result(
        self,
        messages: list[dict[str, Any]],
        strategy: CompactStrategy,
        *,
        bypass_retry_guard: bool = False,
    ) -> CompactionResult | None:
        if bypass_retry_guard:
            return None
        if self.is_tripped:
            return CompactionResult(
                success=False,
                strategy=strategy,
                trigger=CompactTrigger.AUTO,
                messages=list(messages),
                error="Compaction circuit breaker is open",
            )
        failed_digest = self._failed_state_digests.get(strategy)
        # Computing the state digest serializes the whole history; skip it
        # entirely when no failure was ever recorded for this strategy.
        if (
            failed_digest is not None
            and failed_digest == self._message_state_digest(messages)
        ):
            return CompactionResult(
                success=False,
                strategy=strategy,
                trigger=CompactTrigger.AUTO,
                messages=list(messages),
                error=(
                    "Compaction retry suppressed for unchanged message state"
                ),
            )
        return None

    def should_trigger(
        self,
        messages: list[dict[str, Any]],
        token_usage: int | None = None,
    ) -> bool:
        """Check if auto compact should trigger."""
        if not self._config.enabled:
            return False
        if self.is_tripped:
            return False

        usage = token_usage or sum(self._estimate(m) for m in messages)
        return usage >= self.threshold_tokens

    def dispatch(
        self,
        messages: list[dict[str, Any]],
        token_usage: int | None = None,
        force_full: bool = False,
        *,
        bypass_retry_guard: bool = False,
    ) -> CompactionResult:
        """Run auto compact dispatch: try session memory first, then full."""
        blocked = self._blocked_result(
            messages,
            CompactStrategy.FULL,
            bypass_retry_guard=bypass_retry_guard,
        )
        if blocked is not None:
            return blocked
        if not self.should_trigger(messages, token_usage) and not force_full:
            return CompactionResult(
                success=False,
                strategy=CompactStrategy.FULL,
                trigger=CompactTrigger.AUTO,
                messages=messages,
            )

        usage = token_usage or sum(self._estimate(m) for m in messages)
        logger.info(
            "Auto Compact dispatch: usage=%d, threshold=%d, circuit_breaker=%s",
            usage,
            self.threshold_tokens,
            "TRIPPED" if self.is_tripped else "OK",
        )

        # Try Session Memory Compact first (unless forced full)
        if not force_full:
            sm_result = self._session_memory_engine.try_session_memory_compact(
                messages,
                self._context_window,
                self._estimate,
                self._config,
                transcript_summarizer=self._summary_generator,
            )
            if sm_result and sm_result.effective:
                if (
                    sm_result.materialization_state
                    is CompactionMaterializationState.PENDING
                ):
                    sm_result.memory_free_fallback = list(messages)
                self._record_success_or_defer(sm_result)
                return sm_result

        # Fall back to Full Compact
        return self._run_full_compact(messages, usage)

    def execute_selected(
        self,
        messages: list[dict[str, Any]],
        strategy: CompactStrategy,
    ) -> CompactionResult:
        """Execute an already-selected strategy without re-running policy.

        Cybernetic control has already decided whether action is warranted.
        Routing that decision back through the 85% high-water policy made
        ``force_execution`` advisory and allowed FULL to silently become
        SESSION_MEMORY.
        """
        blocked = self._blocked_result(messages, strategy)
        if blocked is not None:
            return blocked
        usage = sum(self._estimate(message) for message in messages)
        if strategy == CompactStrategy.FULL:
            return self._run_full_compact(messages, usage)
        if strategy == CompactStrategy.SESSION_MEMORY:
            result = self._session_memory_engine.try_session_memory_compact(
                messages,
                self._context_window,
                self._estimate,
                self._config,
                transcript_summarizer=self._summary_generator,
            )
            if result is not None and result.effective:
                if (
                    result.materialization_state
                    is CompactionMaterializationState.PENDING
                ):
                    result.memory_free_fallback = list(messages)
                self._record_success_or_defer(result)
                return result
            self._on_failure(messages, CompactStrategy.SESSION_MEMORY)
            return CompactionResult(
                success=False,
                strategy=CompactStrategy.SESSION_MEMORY,
                trigger=CompactTrigger.AUTO,
                messages=list(messages),
                error="Selected Session Memory compaction was unavailable or ineffective",
            )
        return CompactionResult(
            success=False,
            strategy=strategy,
            trigger=CompactTrigger.AUTO,
            messages=list(messages),
            error=f"Unsupported selected strategy: {strategy.value}",
        )

    def _run_full_compact(
        self, messages: list[dict[str, Any]], usage: int
    ) -> CompactionResult:
        """Full compact: generate summary and create new baseline."""
        original_messages = list(messages)
        previous_summary = _previous_compaction_summary(original_messages)
        # Old boundary markers from previous compactions are stripped here:
        # keeping them made the system prompt grow with every compact cycle.
        messages = _strip_old_boundaries(messages)
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= self._config.min_keep_messages:
            self._on_failure(original_messages, CompactStrategy.FULL)
            return CompactionResult(
                success=False,
                strategy=CompactStrategy.FULL,
                trigger=CompactTrigger.AUTO,
                messages=messages,
                error="Too few messages to compact",
            )

        # Priority-aware tail:
        # 1. recent messages (last third, capped at min_keep_messages)
        # 2. never strand a tool_result from its tool call
        # 3. the last dropped user instruction is re-inserted verbatim —
        #    losing the actual task wording is how "compacted model goes dumb"
        #    happens
        # 4. grow the tail back until min_keep_tokens is respected
        tail_size = min(len(non_system) // 3, self._config.min_keep_messages)
        tail_cut = _adjust_tail_cut_for_tool_pairs(
            non_system, len(non_system) - max(tail_size, 0)
        )
        tail = list(non_system[tail_cut:]) if tail_cut < len(non_system) else []
        tail_tokens = sum(self._estimate(m) for m in tail)
        # The floor is capped at half the pre-compaction usage: a floor larger
        # than what is being compacted would pull everything back and turn
        # the compact into pure overhead (marker + reinserted messages).
        effective_floor = min(
            self._config.min_keep_tokens, int(usage * 0.5)
        )
        while (
            tail_tokens < effective_floor
            and tail_cut > 0
        ):
            # Walk the cut back a whole pairing-safe step at a time so the
            # floor never strands tool results either.
            new_cut = _adjust_tail_cut_for_tool_pairs(non_system, tail_cut - 1)
            restored = non_system[new_cut:tail_cut]
            tail_tokens += sum(self._estimate(m) for m in restored)
            tail = restored + tail
            tail_cut = new_cut

        # The final cut is now stable. Protect the last dropped user exactly
        # once, then summarize only messages that will actually be removed.
        protected_index = next(
            (
                index
                for index in range(tail_cut - 1, -1, -1)
                if non_system[index].get("role") == "user"
                and str(non_system[index].get("content", "")).strip()
            ),
            None,
        )
        protected_indices = {
            index
            for index in _loaded_skill_context_indices(non_system)
            if index < tail_cut
        }
        if protected_index is not None:
            protected_indices.add(protected_index)
        if protected_indices:
            tail = [non_system[index] for index in sorted(protected_indices)] + tail
        dropped = [
            message
            for index, message in enumerate(non_system[:tail_cut])
            if index not in protected_indices
        ]
        if not dropped:
            self._on_failure(original_messages, CompactStrategy.FULL)
            return CompactionResult(
                success=False,
                strategy=CompactStrategy.FULL,
                trigger=CompactTrigger.AUTO,
                messages=original_messages,
                error="No messages remain to summarize",
            )

        # Generate a summary from the exact dropped set. The LLM generator
        # chunks large histories so head/middle/tail all enter a summary
        # request. The heuristic is only a fallback; the savings gate below
        # refuses to replace the original context if its output is not useful.
        summary = None
        if self._summary_generator is not None:
            try:
                summary_input = (
                    [previous_summary, *dropped]
                    if previous_summary is not None
                    else dropped
                )
                summary = self._summary_generator.summarize(summary_input)
            except (TurnCancellationRequested, ModelCallDeadlineExceeded):
                raise
            except Exception as exc:  # noqa: BLE001 - fallback is the contract
                logger.warning("LLM summary raised (%s); heuristic fallback", exc)
                summary = None
        if not summary:
            summary_input = (
                [previous_summary, *dropped]
                if previous_summary is not None
                else dropped
            )
            summary = self._generate_structured_summary(summary_input)
        summary = _merge_summary_with_critical_anchors(summary, summary_input)

        boundary = CompactBoundary(
            trigger=CompactTrigger.AUTO,
            strategy=CompactStrategy.FULL,
            tokens_before=usage,
        )

        # Build compacted: system + boundary + exact preserved tail.
        compacted = list(system_msgs)
        compacted.append({
            "role": "system",
            "content": (
                f"[Context compacted at {time.strftime('%H:%M:%S')} — Full Compact]\n"
                f"Original: ~{usage} tokens, {len(messages)} messages\n\n"
                f"## Conversation Summary\n\n{summary}"
            ),
            "_compact_boundary": True,
        })

        compacted.extend(tail)

        boundary.tokens_after = sum(self._estimate(m) for m in compacted)
        boundary.messages_removed = len(messages) - len(compacted)

        if boundary.tokens_after >= boundary.tokens_before:
            self._on_failure(original_messages, CompactStrategy.FULL)
            return CompactionResult(
                success=False,
                strategy=CompactStrategy.FULL,
                trigger=CompactTrigger.AUTO,
                messages=original_messages,
                boundary=boundary,
                tokens_freed=boundary.tokens_before - boundary.tokens_after,
                summary_text=summary,
                error="Compaction did not reduce estimated tokens",
            )

        self._on_success(boundary)
        self._suppress_warnings()

        logger.info(
            "Full Compact: %d → %d tokens (%d removed)",
            boundary.tokens_before,
            boundary.tokens_after,
            boundary.messages_removed,
        )

        return CompactionResult(
            success=True,
            strategy=CompactStrategy.FULL,
            trigger=CompactTrigger.AUTO,
            messages=compacted,
            boundary=boundary,
            tokens_freed=boundary.tokens_before - boundary.tokens_after,
            summary_text=summary,
        )

    def _generate_structured_summary(self, messages: list[dict]) -> str:
        """Generate structured summary from message history without LLM call."""
        parts = ["### Summary of conversation so far:\n"]

        # Extract key information patterns
        previous_summaries = []
        user_topics = []
        tool_calls_made = set()
        files_mentioned = set()
        errors_seen = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if msg.get("_previous_compact_summary") and isinstance(content, str):
                previous_summaries.append(content[:4_000])

            if role == "user" and isinstance(content, str) and len(content) > 10:
                topic = content[:100].replace("\n", " ")
                user_topics.append(topic)

            if role == "assistant" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_calls_made.add(block.get("name", "unknown"))
                        input_data = block.get("input", {})
                        if "file_path" in input_data:
                            files_mentioned.add(input_data["file_path"])

            if role == "tool_result":
                err = msg.get("isError")
                if err:
                    errors_seen.append(content[:80] if isinstance(content, str) else str(content)[:80])

        if previous_summaries:
            parts.append("**Previous compacted context:**\n")
            parts.extend(previous_summaries[-1:])
            parts.append("")

        if user_topics:
            parts.append("**Topics discussed:**\n")
            for t in user_topics[:8]:
                parts.append(f"- {t}")
            parts.append("")

        if tool_calls_made:
            parts.append(f"**Tools used:** {', '.join(sorted(tool_calls_made))}\n")

        if files_mentioned:
            parts.append(f"**Files touched:** {', '.join(sorted(files_mentioned)[:10])}\n")

        if errors_seen:
            parts.append("**Errors encountered:**\n")
            for e in errors_seen[:3]:
                parts.append(f"- {e}")
            parts.append("")

        parts.append("\n*Continue from where we left off.*")
        return "\n".join(parts)

    def _on_success(self, boundary: CompactBoundary | None) -> None:
        self._consecutive_failures = 0
        self._failed_state_digests.clear()
        if boundary:
            self._boundaries.append(boundary)

    def _record_success_or_defer(self, result: CompactionResult) -> None:
        """Advance control state only for an already-materialized result."""
        if (
            result.materialization_state
            is CompactionMaterializationState.PENDING
        ):
            receipt_key = self._materialization_key(result)
            if receipt_key:
                self._remember_materialization_outcome(
                    receipt_key,
                    CompactionMaterializationState.PENDING,
                    overwrite=False,
                )
            logger.debug(
                "Session Memory compaction awaits prompt materialization"
            )
            return
        self._on_success(result.boundary)
        self._suppress_warnings()

    @staticmethod
    def _materialization_key(result: CompactionResult) -> str:
        receipt = result.memory_receipt
        if receipt is None:
            return ""
        receipt_id = str(getattr(receipt, "receipt_id", "") or "")
        return receipt_id or f"legacy-object-{id(receipt)}"

    def _known_materialization_outcome(
        self,
        result: CompactionResult,
    ) -> CompactionMaterializationState | None:
        key = self._materialization_key(result)
        return self._materialization_outcomes.get(key) if key else None

    def _set_materialization_outcome(
        self,
        result: CompactionResult,
        state: CompactionMaterializationState,
    ) -> None:
        key = self._materialization_key(result)
        if not key:
            return
        self._remember_materialization_outcome(key, state)

    def _remember_materialization_outcome(
        self,
        key: str,
        state: CompactionMaterializationState,
        *,
        overwrite: bool = True,
    ) -> None:
        if not overwrite and key in self._materialization_outcomes:
            return
        self._materialization_outcomes[key] = state
        while (
            len(self._materialization_outcomes)
            > self._materialization_outcome_limit
        ):
            oldest_key = next(iter(self._materialization_outcomes))
            self._materialization_outcomes.pop(oldest_key, None)

    def confirm_materialized(self, result: CompactionResult) -> bool:
        """Commit deferred boundary/circuit state after prompt installation."""
        known = self._known_materialization_outcome(result)
        if known in {
            CompactionMaterializationState.INSTALLED,
            CompactionMaterializationState.REJECTED,
        }:
            result.materialization_state = known
            if known is CompactionMaterializationState.REJECTED:
                result.success = False
            return False
        if (
            result.materialization_state
            is not CompactionMaterializationState.PENDING
        ):
            return False
        result.materialization_state = CompactionMaterializationState.INSTALLED
        self._set_materialization_outcome(
            result,
            CompactionMaterializationState.INSTALLED,
        )
        self._on_success(result.boundary)
        self._suppress_warnings()
        return True

    def discard_unmaterialized(
        self,
        result: CompactionResult,
        original_messages: list[dict[str, Any]],
    ) -> bool:
        """Record a rejected candidate as failure, never as compaction success."""
        known = self._known_materialization_outcome(result)
        if known in {
            CompactionMaterializationState.INSTALLED,
            CompactionMaterializationState.REJECTED,
        }:
            result.materialization_state = known
            if known is CompactionMaterializationState.REJECTED:
                result.success = False
                if not result.error:
                    result.error = (
                        "Memory authority changed before compaction install"
                    )
                result.messages = list(
                    result.memory_free_fallback
                    if result.memory_free_fallback is not None
                    else original_messages
                )
                result.tokens_freed = result.memory_free_tokens_freed
            return False
        if (
            result.materialization_state
            is not CompactionMaterializationState.PENDING
        ):
            return False
        result.materialization_state = CompactionMaterializationState.REJECTED
        self._set_materialization_outcome(
            result,
            CompactionMaterializationState.REJECTED,
        )
        result.success = False
        if not result.error:
            result.error = "Memory authority changed before compaction install"
        fallback_messages = list(
            result.memory_free_fallback
            if result.memory_free_fallback is not None
            else original_messages
        )
        result.messages = fallback_messages
        result.tokens_freed = result.memory_free_tokens_freed
        strategy = (
            result.boundary.strategy
            if result.boundary is not None
            else result.strategy
        )
        self._on_failure(fallback_messages, strategy)
        return True

    def _on_failure(
        self,
        messages: list[dict[str, Any]] | None = None,
        strategy: CompactStrategy = CompactStrategy.FULL,
    ) -> None:
        self._consecutive_failures += 1
        if messages is not None:
            self._failed_state_digests[strategy] = self._message_state_digest(
                messages
            )
        logger.warning(
            "Auto Compact failure #%d/%d (circuit breaker)",
            self._consecutive_failures,
            self._config.circuit_breaker_limit,
        )

    def _suppress_warnings(self, duration: float = 30.0) -> None:
        self._suppressed_until = time.time() + duration

    def is_warning_suppressed(self) -> bool:
        return time.time() < self._suppressed_until

    def reset_circuit_breaker(self) -> None:
        self._consecutive_failures = 0
        self._failed_state_digests.clear()

    def get_history(self) -> list[CompactBoundary]:
        return list(self._boundaries)

    def get_last_boundary(self) -> CompactBoundary | None:
        return self._boundaries[-1] if self._boundaries else None


# ---------------------------------------------------------------------------
# Phase 7: Reactive Compact (Error Recovery)
# ---------------------------------------------------------------------------


class ReactiveCompactEngine:
    """Error recovery compaction for post-API-failure scenarios.

    Triggered when the model API rejects a request due to:
    - prompt too long
    - media size exceeded
    - other recoverable errors
    """

    MAX_RETRIES = 3

    def __init__(
        self,
        auto_compact: AutoCompactDispatcher | None = None,
        estimate_fn=None,
    ):
        self._auto_compact = auto_compact
        self._estimate = estimate_fn or (lambda m: len(str(m)) // 4)
        self._recovery_attempts = 0

    def try_recover_from_overflow(
        self,
        messages: list[dict[str, Any]],
        error_message: str = "",
    ) -> CompactionResult | None:
        """Attempt recovery from prompt-too-long error.

        Strategy:
        1. Force Full Compact with aggressive truncation
        2. If still too long, drop oldest API round groups
        3. Up to MAX_RETRIES attempts
        """
        self._recovery_attempts += 1
        if self._recovery_attempts > self.MAX_RETRIES:
            logger.error("Reactive Compact: max retries (%d) exceeded", self.MAX_RETRIES)
            return None

        logger.info(
            "Reactive Compact attempt %d/%d: recovering from overflow",
            self._recovery_attempts,
            self.MAX_RETRIES,
        )

        # Use auto compact with force_full
        if self._auto_compact:
            # A provider overflow is hard new evidence: the breaker verdict
            # from proactive compaction is superseded for this recovery
            # attempt (and stays cleared if recovery succeeds, giving
            # proactive compaction a fresh start).
            if self._auto_compact.is_tripped:
                self._auto_compact.reset_circuit_breaker()

            # A provider overflow is new hard evidence that warrants one
            # reactive attempt even if the same state previously failed a
            # proactive compaction policy check.
            result = self._auto_compact.dispatch(
                messages,
                force_full=True,
                bypass_retry_guard=True,
            )

            # Check if result is small enough
            result_usage = sum(self._estimate(m) for m in result.messages)
            if result_usage < self._auto_compact.blocking_limit * 0.9:
                self._recovery_attempts = 0  # Reset on success
                return CompactionResult(
                    success=True,
                    strategy=CompactStrategy.REACTIVE,
                    trigger=CompactTrigger.REACTIVE,
                    messages=result.messages,
                    boundary=result.boundary,
                    tokens_freed=result.tokens_freed,
                )

        # Aggressive fallback: truncate oldest messages directly
        # Only attempt if still within retry budget
        if self._recovery_attempts > self.MAX_RETRIES:
            logger.error("Reactive Compact: max retries (%d) exceeded in fallback", self.MAX_RETRIES)
            return None
        return self._aggressive_truncate(messages)

    def _aggressive_truncate(
        self, messages: list[dict[str, Any]]
    ) -> CompactionResult:
        """Aggressively truncate to fit within limits."""
        previous_summary = _previous_compaction_summary(messages)
        messages = _strip_old_boundaries(messages)
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # Keep only most recent portion
        keep_ratio = 0.4 - (self._recovery_attempts * 0.1)  # Progressive truncation
        keep_count = max(3, int(len(non_system) * max(keep_ratio, 0.15)))

        truncated = list(system_msgs)
        truncated.append({
            "role": "system",
            "content": (
                f"[Context aggressively truncated for recovery — attempt {self._recovery_attempts}]\n"
                f"Earlier conversation was removed to fit context limits."
                + (
                    "\n\n## Previous compacted context\n\n"
                    + str(previous_summary.get("content", ""))
                    if previous_summary is not None
                    else ""
                )
            ),
            "_reactive_compact": True,
        })
        keep_cut = _adjust_tail_cut_for_tool_pairs(
            non_system, len(non_system) - keep_count
        )
        protected_skill_indices = sorted(
            index
            for index in _loaded_skill_context_indices(non_system)
            if index < keep_cut
        )
        truncated.extend(non_system[index] for index in protected_skill_indices)
        truncated.extend(non_system[keep_cut:])

        boundary = CompactBoundary(
            trigger=CompactTrigger.REACTIVE,
            strategy=CompactStrategy.REACTIVE,
            tokens_before=sum(self._estimate(m) for m in messages),
            tokens_after=sum(self._estimate(m) for m in truncated),
            messages_removed=len(messages) - len(truncated),
        )

        return CompactionResult(
            success=True,
            strategy=CompactStrategy.REACTIVE,
            trigger=CompactTrigger.REACTIVE,
            messages=truncated,
            boundary=boundary,
            tokens_freed=boundary.tokens_before - boundary.tokens_after,
        )


# ---------------------------------------------------------------------------
# Unified Context Manager (Orchestrates all phases)
# ---------------------------------------------------------------------------


class ContextCompactor:
    """Unified context management orchestrator.

    Implements the complete Claude Code-style pipeline:

    Step 1: Construct active context (from last boundary)
    Step 2: Apply tool result budget
    Step 3: Read dedup (applied at tool-result time, not in this pipeline)
    Step 4: Microcompact
    Step 5: Auto Compact high-water check
    Step 6: Dispatch (Session Memory → Full)
    Step 7: Reactive recovery (if needed)
    """

    def __init__(
        self,
        context_window: int = 200000,
        workspace: str | Path | None = None,
        memory_manager=None,
        estimate_fn=None,
        config: AutoCompactConfig | None = None,
        summary_generator: Any = None,
    ):
        self._context_window = context_window
        self._workspace = Path(workspace) if workspace else Path.cwd()
        self._config = config or AutoCompactConfig()

        self._tool_budget = ToolResultBudgetManager(workspace)
        self._read_dedup = ReadDedupManager()
        self._microcompact = MicrocompactEngine()
        self._auto_compact = AutoCompactDispatcher(
            context_window=context_window,
            config=config,
            memory_manager=memory_manager,
            estimate_fn=estimate_fn,
            summary_generator=summary_generator,
        )
        self._reactive = ReactiveCompactEngine(self._auto_compact, estimate_fn)
        self._estimate = estimate_fn or (lambda m: len(str(m)) // 4)

        self._last_compact_result: CompactionResult | None = None
        self._total_optimization_passes = 0
        # Observational reconciliation of provider-reported input tokens
        # against the local estimate that drives the 85% high-water policy.
        self._calibration_samples = 0
        self._calibration_ratio = 1.0
        self._calibration_min_ratio: float | None = None
        self._calibration_max_ratio: float | None = None
        self._calibration_last_reported = 0
        self._calibration_last_estimated = 0
        self._calibration_drift_warned = False

    _CALIBRATION_MIN_ESTIMATED_TOKENS = 1000
    _CALIBRATION_EWMA_ALPHA = 0.2
    _CALIBRATION_RATIO_BOUNDS = (0.25, 4.0)

    def record_usage_observation(
        self,
        reported_input_tokens: int,
        estimated_tokens: int,
    ) -> None:
        """Reconcile provider-reported input tokens with the local estimate.

        Purely observational: it feeds ``get_stats()`` and a one-time drift
        warning so a systematically wrong estimator is visible in
        production; it never changes compaction decisions.
        """
        if (
            not isinstance(reported_input_tokens, int)
            or not isinstance(estimated_tokens, int)
            or reported_input_tokens <= 0
            or estimated_tokens < self._CALIBRATION_MIN_ESTIMATED_TOKENS
        ):
            return
        ratio = reported_input_tokens / estimated_tokens
        low, high = self._CALIBRATION_RATIO_BOUNDS
        if not low <= ratio <= high:
            # An absurd sample (provider bug, wrong attribution window)
            # would poison the EWMA; ignore it entirely.
            return
        self._calibration_samples += 1
        self._calibration_ratio = (
            self._CALIBRATION_EWMA_ALPHA * ratio
            + (1 - self._CALIBRATION_EWMA_ALPHA) * self._calibration_ratio
        )
        self._calibration_min_ratio = (
            ratio
            if self._calibration_min_ratio is None
            else min(self._calibration_min_ratio, ratio)
        )
        self._calibration_max_ratio = (
            ratio
            if self._calibration_max_ratio is None
            else max(self._calibration_max_ratio, ratio)
        )
        self._calibration_last_reported = reported_input_tokens
        self._calibration_last_estimated = estimated_tokens
        if (
            not self._calibration_drift_warned
            and self._calibration_samples >= 5
            and abs(self._calibration_ratio - 1.0) > 0.25
        ):
            self._calibration_drift_warned = True
            logger.warning(
                "Context token estimate drifts from provider usage: "
                "ratio %.2f over %d samples (last: reported=%d, "
                "estimated=%d); the auto-compact threshold may fire "
                "early or late",
                self._calibration_ratio,
                self._calibration_samples,
                reported_input_tokens,
                estimated_tokens,
            )

    def process_request(
        self,
        messages: list[dict[str, Any]],
        *,
        enable_tool_budget: bool = True,
        enable_microcompact: bool = True,
        enable_auto_compact: bool = True,
    ) -> CompactionResult:
        """Run the full pre-request optimization pipeline.

        This is the main entry point called before each API request.
        Read dedup is applied at tool-result processing time (see
        ``ReadDedupManager``), not in this pipeline.
        """
        self._total_optimization_passes += 1
        self._last_compact_result = None
        current = list(messages)
        total_freed = 0
        steps_taken = []
        memory_receipt: MemoryPromptReceipt | None = None
        compact_boundary: CompactBoundary | None = None
        materialization_state = CompactionMaterializationState.NOT_REQUIRED
        memory_free_fallback: list[dict[str, Any]] | None = None
        memory_free_tokens_freed = 0
        memory_free_strategy: CompactStrategy | None = None
        memory_free_messages_before = len(messages)
        memory_free_messages_after = len(messages)
        result_strategy = CompactStrategy.FULL

        # Step 2: Tool Result Budget
        if enable_tool_budget:
            current, budget_saved = self._tool_budget.check_and_replace(current)
            if budget_saved > 0:
                # budget_saved is characters; report the rest of the pipeline
                # in (estimated) tokens, so convert with the same ~4 chars per
                # token heuristic the estimator uses.
                total_freed += budget_saved // 4
                steps_taken.append(f"tool_budget({budget_saved // 4})")
                result_strategy = CompactStrategy.TOOL_BUDGET

        # Step 4: Microcompact
        if enable_microcompact:
            mc_result = self._microcompact.run_time_based_microcompact(current)
            if mc_result.effective:
                current = mc_result.messages
                total_freed += mc_result.tokens_freed
                steps_taken.append(f"microcompact({mc_result.tokens_freed})")
                result_strategy = (
                    CompactStrategy.PARTIAL
                    if result_strategy is CompactStrategy.TOOL_BUDGET
                    else CompactStrategy.MICROCOMPACT
                )

        memory_free_tokens_freed = total_freed
        if memory_free_tokens_freed > 0:
            memory_free_strategy = result_strategy
            memory_free_messages_after = len(current)

        # Step 5+6: Auto Compact high-water dispatch
        if enable_auto_compact and self._auto_compact.should_trigger(current):
            ac_result = self._auto_compact.dispatch(current)
            self._last_compact_result = ac_result
            if ac_result.effective:
                current = ac_result.messages
                total_freed += ac_result.tokens_freed
                memory_receipt = ac_result.memory_receipt
                compact_boundary = ac_result.boundary
                materialization_state = ac_result.materialization_state
                memory_free_fallback = ac_result.memory_free_fallback
                steps_taken.append(f"auto_compact({ac_result.strategy.value},{ac_result.tokens_freed})")
                result_strategy = ac_result.strategy

        # Liveness reconciliation is a safety invariant, not an optional
        # optimization. Even callers that disable new dedup processing may
        # compact away a source referenced by an existing cache entry.
        if materialization_state is not CompactionMaterializationState.PENDING:
            self._read_dedup.reconcile(current)

        result = CompactionResult(
            success=total_freed > 0,
            strategy=result_strategy,
            trigger=CompactTrigger.AUTO,
            messages=current,
            boundary=compact_boundary,
            tokens_freed=total_freed,
            summary_text=f"Optimization steps: {' + '.join(steps_taken)}" if steps_taken else "",
            memory_receipt=memory_receipt,
            materialization_state=materialization_state,
            memory_free_fallback=memory_free_fallback,
            memory_free_tokens_freed=(
                memory_free_tokens_freed
                if materialization_state
                is CompactionMaterializationState.PENDING
                else 0
            ),
            memory_free_strategy=(
                memory_free_strategy
                if materialization_state
                is CompactionMaterializationState.PENDING
                else None
            ),
            memory_free_messages_before=memory_free_messages_before,
            memory_free_messages_after=memory_free_messages_after,
        )

        logger.info(
            "ContextCompactor pass #%d: %d tokens freed across [%s]",
            self._total_optimization_passes,
            total_freed,
            ", ".join(steps_taken) if steps_taken else "none",
        )

        return result

    def reactive_recover(
        self, messages: list[dict[str, Any]], error: str = ""
    ) -> CompactionResult | None:
        """Attempt reactive recovery after API error."""
        result = self._reactive.try_recover_from_overflow(messages, error)
        if result is not None and result.effective:
            self._read_dedup.reconcile(result.messages)
        return result

    def execute_strategy(
        self,
        messages: list[dict[str, Any]],
        strategy: CompactStrategy,
    ) -> CompactionResult:
        """Actuate one strategy selected by the cybernetic controller."""
        current = list(messages)
        if strategy == CompactStrategy.MICROCOMPACT:
            result = self._microcompact.run_time_based_microcompact(current)
        elif strategy in (CompactStrategy.FULL, CompactStrategy.SESSION_MEMORY):
            result = self._auto_compact.execute_selected(current, strategy)
        else:
            result = CompactionResult(
                success=False,
                strategy=strategy,
                trigger=CompactTrigger.AUTO,
                messages=current,
                error=f"Unsupported cybernetic strategy: {strategy.value}",
            )
        if result.effective:
            self._last_compact_result = result
            if (
                result.materialization_state
                is not CompactionMaterializationState.PENDING
            ):
                self._read_dedup.reconcile(result.messages)
        return result

    def confirm_materialized(self, result: CompactionResult) -> bool:
        """Finalize deferred compactor state after an atomic prompt install."""
        confirmed = self._auto_compact.confirm_materialized(result)
        if confirmed:
            last_result = self._last_compact_result
            if (
                last_result is not None
                and last_result is not result
                and last_result.memory_receipt is result.memory_receipt
            ):
                last_result.materialization_state = (
                    CompactionMaterializationState.INSTALLED
                )
            self._read_dedup.reconcile(result.messages)
        return confirmed

    def discard_unmaterialized(
        self,
        result: CompactionResult,
        original_messages: list[dict[str, Any]],
    ) -> bool:
        """Discard a fenced candidate and restore caches to live messages."""
        discarded = self._auto_compact.discard_unmaterialized(
            result,
            original_messages,
        )
        if discarded:
            last_result = self._last_compact_result
            if (
                last_result is not None
                and last_result is not result
                and last_result.memory_receipt is result.memory_receipt
            ):
                last_result.materialization_state = (
                    CompactionMaterializationState.REJECTED
                )
                last_result.success = False
                last_result.error = (
                    "Memory authority changed before compaction install"
                )
                last_result.messages = list(result.messages)
                last_result.memory_free_fallback = list(result.messages)
                last_result.memory_free_tokens_freed = (
                    result.memory_free_tokens_freed
                )
                last_result.memory_free_strategy = result.memory_free_strategy
                last_result.memory_free_messages_before = (
                    result.memory_free_messages_before
                )
                last_result.memory_free_messages_after = (
                    result.memory_free_messages_after
                )
                last_result.tokens_freed = result.tokens_freed
            self._read_dedup.reconcile(result.messages)
        return discarded

    @staticmethod
    def rejection_fallback_messages(
        result: CompactionResult,
        original_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return the exact Memory-free view safe after receipt rejection."""
        return list(
            result.memory_free_fallback
            if result.memory_free_fallback is not None
            else original_messages
        )

    def compact_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """Run a compaction pass and return the replacement messages.

        ``force=True`` dispatches the auto-compact strategy even below the
        high-water mark, for outer-loop / predictive callers that already
        decided compaction is needed.
        """
        current = list(messages)
        if force:
            result = self._auto_compact.dispatch(current, force_full=True)
            self._last_compact_result = result
            if result.effective:
                self._read_dedup.reconcile(result.messages)
                return result.messages
            return current
        result = self.process_request(current)
        if (
            result.materialization_state
            is CompactionMaterializationState.PENDING
        ):
            # This compatibility API has no atomic prompt-install callback.
            # Returning a Memory-bearing candidate would bypass the authority
            # fence, so refuse that candidate and return only the already-safe
            # tool-budget/microcompact prefix.
            self.discard_unmaterialized(result, current)
            return list(result.messages)
        return result.messages if result.effective else current

    @property
    def tool_budget(self) -> ToolResultBudgetManager:
        return self._tool_budget

    @property
    def read_dedup(self) -> ReadDedupManager:
        return self._read_dedup

    @property
    def auto_compact(self) -> AutoCompactDispatcher:
        return self._auto_compact

    @property
    def reactive(self) -> ReactiveCompactEngine:
        return self._reactive

    @property
    def last_result(self) -> CompactionResult | None:
        return self._last_compact_result

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_passes": self._total_optimization_passes,
            "tool_results_persisted": self._tool_budget.get_persisted_count(),
            "tool_bytes_saved": self._tool_budget.get_total_saved_bytes(),
            "read_dedup_entries": len(self._read_dedup._entries),
            "microcompact_tokens_cleared": self._microcompact._state.total_tokens_cleared,
            "auto_compact_boundaries": len(self._auto_compact.get_history()),
            "circuit_breaker_tripped": self._auto_compact.is_tripped,
            "reactive_recovery_attempts": self._reactive._recovery_attempts,
            "context_window": self._context_window,
            "auto_compact_threshold": self._auto_compact.threshold_tokens,
            "token_calibration_samples": self._calibration_samples,
            "token_calibration_ratio": round(self._calibration_ratio, 3),
            "token_calibration_min_ratio": (
                round(self._calibration_min_ratio, 3)
                if self._calibration_min_ratio is not None
                else None
            ),
            "token_calibration_max_ratio": (
                round(self._calibration_max_ratio, 3)
                if self._calibration_max_ratio is not None
                else None
            ),
        }

    def format_pipeline_status(self) -> str:
        stats = self.get_stats()
        lines = [
            "Context Management Pipeline Status",
            "=" * 40,
            f"Optimization passes: {stats['total_passes']}",
            f"Tool results persisted: {stats['tool_results_persisted']} ({stats['tool_bytes_saved']} bytes saved)",
            f"Read dedup cache: {stats['read_dedup_entries']} files",
            f"Microcompact cleared: ~{stats['microcompact_tokens_cleared']} tokens",
            f"Compact boundaries: {stats['auto_compact_boundaries']}",
            f"Circuit breaker: {'TRIPPED' if stats['circuit_breaker_tripped'] else 'OK'}",
            f"Reactive recoveries: {stats['reactive_recovery_attempts']}",
            "",
            f"Context window: {stats['context_window']:,} tokens",
            f"Auto compact threshold: {stats['auto_compact_threshold']:,} tokens ({self._config.threshold_ratio:.0%})",
        ]
        return "\n".join(lines)
