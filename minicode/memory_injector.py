from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from minicode.memory import MemoryManager
from minicode.logging_config import get_logger

logger = get_logger("memory_injector")


@dataclass
class InjectedMemory:
    """A memory entry prepared for injection into context."""
    content: str
    category: str
    relevance_score: float
    source: str  # "search", "tag", "category"
    entry_id: str = ""
    scope: str = ""


class MemoryInjectionMode(str, Enum):
    NONE = "none"
    SUMMARY = "summary"
    STANDARD = "standard"
    STRONG = "strong"


@dataclass
class MemoryInjectionSignal:
    """Observed state for memory-injection control."""

    context_usage: float = 0.0
    retrieval_quality: float = 0.5
    user_correction_count: int = 0
    recent_failure: bool = False
    task_repetition: bool = False
    active_domains: list[str] = field(default_factory=list)


@dataclass
class MemoryInjectionDecision:
    """Controller output for memory injection."""

    mode: MemoryInjectionMode
    max_memories: int
    min_relevance: float
    max_tokens_per_memory: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "max_memories": self.max_memories,
            "min_relevance": round(self.min_relevance, 3),
            "max_tokens_per_memory": self.max_tokens_per_memory,
            "reasons": list(self.reasons),
        }


class MemoryInjectionController:
    """Feedback controller for how much memory to inject."""

    def decide(
        self,
        signal: MemoryInjectionSignal,
        *,
        base_max_memories: int,
        base_min_relevance: float,
        base_max_tokens: int,
    ) -> MemoryInjectionDecision:
        reasons: list[str] = []
        max_memories = base_max_memories
        min_relevance = base_min_relevance
        max_tokens = base_max_tokens
        mode = MemoryInjectionMode.STANDARD

        if signal.context_usage >= 0.90:
            mode = MemoryInjectionMode.NONE
            reasons.append("critical context pressure")
            return MemoryInjectionDecision(mode, 0, 1.0, 0, reasons)

        if signal.context_usage >= 0.75:
            mode = MemoryInjectionMode.SUMMARY
            max_memories = max(1, min(max_memories, 2))
            max_tokens = max(40, min(max_tokens, 80))
            min_relevance = max(min_relevance, 0.55)
            reasons.append("high context pressure")

        if signal.retrieval_quality < 0.35:
            max_memories = max(1, max_memories - 2)
            min_relevance = max(min_relevance, 0.50)
            reasons.append("low retrieval quality")
        elif signal.retrieval_quality >= 0.75 and signal.context_usage < 0.65:
            mode = MemoryInjectionMode.STRONG
            max_memories = min(base_max_memories + 2, max_memories + 2)
            min_relevance = max(0.15, min_relevance - 0.10)
            reasons.append("high retrieval quality")

        if signal.recent_failure:
            mode = MemoryInjectionMode.STRONG if signal.context_usage < 0.75 else mode
            max_memories = min(base_max_memories + 1, max_memories + 1)
            min_relevance = max(0.15, min_relevance - 0.10)
            reasons.append("recent failure recovery")

        if signal.user_correction_count > 0:
            max_memories = max(1, max_memories - signal.user_correction_count)
            min_relevance = min(0.90, min_relevance + 0.10 * signal.user_correction_count)
            reasons.append("user corrections indicate memory risk")

        if signal.task_repetition and signal.context_usage < 0.80:
            max_memories = min(base_max_memories + 1, max_memories + 1)
            reasons.append("repeated task can reuse memory")

        max_memories = max(0, min(base_max_memories + 2, max_memories))
        max_tokens = max(0, max_tokens)
        min_relevance = max(0.0, min(1.0, min_relevance))

        if max_memories == 0:
            mode = MemoryInjectionMode.NONE

        return MemoryInjectionDecision(
            mode=mode,
            max_memories=max_memories,
            min_relevance=min_relevance,
            max_tokens_per_memory=max_tokens,
            reasons=reasons or ["standard memory injection"],
        )


class MemoryInjector:
    """Injects relevant memories into agent context based on task content."""

    def __init__(
        self,
        memory_manager: MemoryManager | None = None,
        max_injected_memories: int = 5,
        min_relevance: float = 0.3,
        max_tokens_per_memory: int = 200,
        injection_cooldown: float | None = None,
        controller: MemoryInjectionController | None = None,
        reranker: Any | None = None,
    ):
        self._memory = memory_manager
        self._max_injected = max_injected_memories
        self._min_relevance = min_relevance
        self._max_tokens = max_tokens_per_memory
        self._controller = controller or MemoryInjectionController()
        self._reranker = reranker
        self._last_decision: MemoryInjectionDecision | None = None
        self._last_query: str = ""
        self._last_injection_time: float = 0.0
        self._injection_cooldown: float = injection_cooldown if injection_cooldown is not None else 30.0
        self._task_hash: str = ""
        self._cached_result: list[InjectedMemory] = []
        self._last_rerank_result: Any = None
        from minicode.memory_retrieval import CanonicalMemoryRetriever

        self._retriever = (
            CanonicalMemoryRetriever(
                memory_manager,
                controller=self._controller,
            )
            if memory_manager is not None
            else None
        )
        self._last_result: Any = None

    @property
    def retriever(self):
        """Canonical retriever used by this compatibility facade."""
        return self._retriever

    @staticmethod
    def _hash_task(task_description: str, current_files: tuple[str, ...] | None) -> str:
        """Compute a fast hash for cache key."""
        h = hashlib.md5(task_description.encode(), usedforsecurity=False)
        if current_files:
            for f in current_files:
                h.update(f.encode())
        return h.hexdigest()

    def inject_for_task(
        self,
        task_description: str,
        current_files: list[str] | None = None,
        signal: MemoryInjectionSignal | None = None,
    ) -> list[InjectedMemory]:
        """Search and prepare relevant memories for a task.

        Args:
            task_description: Description of the current task
            current_files: List of files currently being worked on

        Returns:
            List of injected memories sorted by relevance
        """
        if self._memory is None:
            return []

        control_signal = signal or MemoryInjectionSignal()
        decision = self._controller.decide(
            control_signal,
            base_max_memories=self._max_injected,
            base_min_relevance=self._min_relevance,
            base_max_tokens=self._max_tokens,
        )
        self._last_decision = decision
        if decision.mode == MemoryInjectionMode.NONE or self._retriever is None:
            self._last_result = None
            return []

        task_hash = self._hash_task(task_description, tuple(current_files) if current_files else None)
        if time.time() - self._last_injection_time < self._injection_cooldown:
            if task_description == self._last_query:
                self._last_result = None
                return []

        if task_hash == self._task_hash and self._cached_result:
            return self._cached_result.copy()

        self._last_query = task_description
        self._last_injection_time = time.time()

        from minicode.memory_retrieval import MemoryRetrievalRequest, RetrievalSource

        result = self._retriever.retrieve(
            MemoryRetrievalRequest(
                query=task_description,
                current_files=tuple(current_files or ()),
                active_domains=tuple(control_signal.active_domains),
                context_usage=control_signal.context_usage,
                max_memories=self._max_injected,
                max_total_tokens=max(0, self._max_injected * self._max_tokens + 64),
                max_tokens_per_memory=self._max_tokens,
                min_relevance=self._min_relevance,
                source_entrypoint=RetrievalSource.INJECTOR,
                recent_failure=control_signal.recent_failure,
            )
        )
        self._last_result = result
        injected = [
            InjectedMemory(
                content=memory.content,
                category=memory.category,
                relevance_score=memory.score.final_score,
                source=memory.source,
                entry_id=memory.entry_id,
                scope=memory.scope,
            )
            for memory in result.rendered
        ]

        logger.info(
            "Injected %d memories for task: %s",
            len(injected),
            task_description[:50],
        )

        self._task_hash = task_hash
        self._cached_result = injected.copy()
        return injected

    def inject_on_failure(
        self,
        error_message: str,
        tool_name: str,
        signal: MemoryInjectionSignal | None = None,
    ) -> list[InjectedMemory]:
        """Search for similar past failures and solutions.

        Args:
            error_message: The error message from the failed tool
            tool_name: Name of the tool that failed

        Returns:
            List of relevant memories that might contain solutions
        """
        if self._memory is None or self._retriever is None:
            return []

        control_signal = signal or MemoryInjectionSignal(recent_failure=True, retrieval_quality=0.6)
        self._last_decision = self._controller.decide(
            control_signal,
            base_max_memories=self._max_injected,
            base_min_relevance=min(0.2, self._min_relevance),
            base_max_tokens=self._max_tokens,
        )
        if self._last_decision.mode == MemoryInjectionMode.NONE:
            self._last_result = None
            return []
        from minicode.memory_retrieval import MemoryRetrievalRequest, RetrievalSource

        result = self._retriever.retrieve(
            MemoryRetrievalRequest(
                query=f"{tool_name} {error_message[:100]}",
                active_domains=tuple(control_signal.active_domains),
                context_usage=control_signal.context_usage,
                max_memories=self._max_injected,
                max_total_tokens=max(0, self._max_injected * self._max_tokens + 64),
                max_tokens_per_memory=self._max_tokens,
                min_relevance=min(0.2, self._min_relevance),
                source_entrypoint=RetrievalSource.FAILURE_RECOVERY,
                recent_failure=True,
            )
        )
        self._last_result = result
        return [
            InjectedMemory(
                content=memory.content,
                category=memory.category,
                relevance_score=memory.score.final_score,
                source=memory.source,
                entry_id=memory.entry_id,
                scope=memory.scope,
            )
            for memory in result.rendered
        ]

    def _record_injected(self, memories: list[InjectedMemory]) -> None:
        """Persist that these concrete memory entries were actually injected."""
        if self._memory is None or not hasattr(self._memory, "record_injections"):
            return
        entry_ids = [m.entry_id for m in memories if m.entry_id]
        if entry_ids:
            self._memory.record_injections(entry_ids)

    @property
    def last_decision(self) -> MemoryInjectionDecision | None:
        return self._last_decision

    def format_for_prompt(self, memories: list[InjectedMemory]) -> str:
        """Format injected memories for inclusion in system prompt.

        Args:
            memories: List of memories to format

        Returns:
            Formatted string for prompt injection
        """
        if not memories:
            return ""

        from minicode.memory_retrieval import (
            MEMORY_DIRECT_VERIFICATION_POLICY,
            MEMORY_INJECTION_FOOTER,
            MEMORY_USE_POLICY,
        )

        lines = ["## Relevant Context from Memory", ""]

        for i, mem in enumerate(memories, 1):
            lines.append(f"{i}. [{mem.category}] {mem.content}")

        lines.extend(
            (
                MEMORY_INJECTION_FOOTER,
                "",
                MEMORY_DIRECT_VERIFICATION_POLICY,
                "",
                MEMORY_USE_POLICY,
            )
        )

        return "\n".join(lines)
