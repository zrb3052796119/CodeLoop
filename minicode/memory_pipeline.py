"""Memory Pipeline — unified facade for the complete memory lifecycle.

Design principle: ONE class, FOUR methods. All memory operations flow
through this single entry point. No scattered ad-hoc calls.

Architecture:
  MemoryPipeline
    ├── read(task, files) → DomainClassifier → canonical BM25 retrieval
    ├── inject(task, files, messages) → read + append to system prompt
    ├── write(task, trace) → ReflectionEngine → TaskContext → MemoryManager
    └── maintain() → CuratorAgent → consolidate/validate/promote/link

Sub-components (internal, not exposed):
  - DomainClassifier: auto-detects active domains from files/intent
  - MemoryInjector: PID-controlled injection into prompt
  - MemoryCuratorAgent: background optimization during idle

Hybrid retrieval is evidence-gated. A request with missing, stale, malformed,
or failing promotion evidence stays on the byte-equivalent lexical path.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from minicode.logging_config import get_logger
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.run_events import verification_corroboration
from minicode.turn_cancellation import TurnCancellationRequested

logger = get_logger("memory_pipeline")


# Durable signals that carry their own independent verification chain.
# Lessons wearing one of these are auto-approved on write; the rest still
# wait for explicit user review.
_CONVERSATION_FACT_TECHNICAL_TERMS = frozenset(
    {
        "代码", "文件", "函数", "类", "模块", "项目", "测试", "修复", "实现",
        "添加", "删除", "运行", "安装", "配置", "部署", "重构", "优化",
        "调试", "报错", "错误", "bug", "api", "python", "javascript",
        "typescript", "react", "docker", "git", "pytest", "sql",
    }
)
_CONVERSATION_FACT_ZH_MARKERS = (
    "是", "喜欢", "不喜欢", "讨厌", "爱", "住在", "在", "来自", "叫",
    "有", "想", "希望", "偏好", "使用", "唯一", "最好", "工作",
)

_STRONG_DURABLE_SIGNALS = frozenset(
    {
        "confirmed_error_recovery_verified",
        "verified_solution",
        "verified_approach",
        "stable_project_constraint",
        "stable_verification_rule",
        "user_correction",
        "old_memory_disproved",
    }
)


def assess_trace_memory_safety(execution_trace: list[dict[str, Any]]):
    """Scan untrusted trace text before any automatic reflection write."""
    from minicode.memory import (
        MemorySafetyResult,
        assess_memory_safety,
        persistence_text_contains_secret,
    )
    from minicode.reflection_evidence import TRACE_MAX_EVENTS

    def iter_strings(value: Any, depth: int = 0):
        if depth > 4:
            return
        if isinstance(value, str):
            yield value[:2000]
        elif isinstance(value, dict):
            for nested in value.values():
                yield from iter_strings(nested, depth + 1)
        elif isinstance(value, list):
            for nested in value[:20]:
                yield from iter_strings(nested, depth + 1)

    for event in execution_trace[:TRACE_MAX_EVENTS]:
        for text in iter_strings(event):
            # Empty strings in structured tool input represent an omitted or
            # root/default argument (for example ``list_files(path="")``).
            # They contain no untrusted instruction text to assess.  Keep the
            # stricter empty-content rejection in ``assess_memory_safety`` for
            # actual durable Memory bodies.
            if not text.strip():
                continue
            if persistence_text_contains_secret(text):
                return MemorySafetyResult(
                    "suspicious",
                    "trace contains credential-like text requiring redaction",
                    "medium",
                )
            safety = assess_memory_safety(text, source="trace")
            if not safety.allowed:
                return MemorySafetyResult(
                    "suspicious",
                    f"trace contains untrusted unsafe-looking text: {safety.reason}",
                    "medium",
                )
    return MemorySafetyResult("safe", "", "low")


class MemoryPipeline:
    """Unified memory operations facade.

    Usage:
        pipeline = MemoryPipeline(memory_manager)
        pipeline.initialize(model_adapter, workspace_path)

        # On task start
        memories = pipeline.read("Create login form", ["src/Login.tsx"])
        messages = pipeline.inject("Create login form", ["src/Login.tsx"], messages)

        # On task end
        pipeline.write("Create login form", execution_trace)

        # Background (every ~10 tasks)
        report = pipeline.maintain()
    """

    def __init__(
        self,
        memory_manager: Any | None = None,
        *,
        hybrid_provider_factory: Any | None = None,
    ):
        self._memory = memory_manager
        self._model: Any = None
        self._workspace: str | None = None

        # Subsystems (lazy init via initialize())
        self._reranker: Any = None
        self._injector: Any = None
        self._curator: Any = None
        self._reflection: Any = None
        self._vector_store: Any = None
        self._dense_store: Any = None
        self._retriever: Any = None
        self._domain_classifier_loaded = False

        self._initialized = False
        self._read_count = 0
        self._write_count = 0
        self._maintain_count = 0
        self._last_injected_ids: list[str] = []
        self._last_written_ids: list[str] = []
        self._last_retrieval_result: Any = None
        self._feedback_recorded = False
        self._project_facts: Any = None
        self._hybrid_activation: Any = None
        self._hybrid_verifier_binding = "not_applicable"
        self._hybrid_provider_factory = hybrid_provider_factory

    # ── Lifecycle ──────────────────────────────────────────────────

    def initialize(
        self,
        model_adapter: Any | None = None,
        workspace_path: str | None = None,
        enable_reranker: bool = False,
        enable_vector: bool = False,
        reflection_engine: Any | None = None,
        hybrid_model_path: str | Path | None = None,
        hybrid_evidence_path: str | Path | None = None,
        hybrid_embedding_provider: str = "local-e5",
        allow_remote_memory_embedding: bool = False,
        hybrid_verifier_binding: str = "",
    ) -> None:
        """Initialize all subsystems. Call once after MemoryManager is ready."""
        if enable_reranker:
            raise ValueError(
                "enable_reranker cannot be enabled: it is not part of "
                "canonical retrieval"
            )
        from minicode.memory_hybrid import assess_hybrid_activation

        self._hybrid_activation = assess_hybrid_activation(
            requested=enable_vector,
            evidence_path=hybrid_evidence_path,
            model_path=hybrid_model_path,
            embedding_provider=hybrid_embedding_provider,
            allow_remote_embedding=allow_remote_memory_embedding,
        )
        self._model = model_adapter
        self._workspace = workspace_path
        self._hybrid_verifier_binding = (
            str(hybrid_verifier_binding).strip()[:64]
            or ("configured_match" if self._hybrid_activation.active else "not_applicable")
        )

        # Deterministic project facts live outside the lesson pipeline.
        if workspace_path:
            from minicode.project_facts import ProjectFactsStore
            self._project_facts = ProjectFactsStore(workspace_path)

        # Injector (PID-controlled injection)
        if self._memory:
            from minicode.memory_injector import MemoryInjector
            self._injector = MemoryInjector(
                memory_manager=self._memory,
                reranker=None,
            )
            self._retriever = self._injector.retriever
            if self._hybrid_activation.active:
                from dataclasses import replace
                from minicode.memory_retrieval import CanonicalMemoryRetriever

                if model_adapter is None:
                    self._hybrid_activation = replace(
                        self._hybrid_activation,
                        active=False,
                        reason="verifier_adapter_unavailable",
                    )

                if self._hybrid_activation.active:
                    factory = self._hybrid_provider_factory
                    using_default_factory = factory is None
                    if factory is None:
                        try:
                            from minicode.memory_hybrid_runtime import (
                                create_hybrid_candidate_provider,
                            )

                            factory = create_hybrid_candidate_provider
                        except Exception:
                            factory = None
                    try:
                        provider = (
                            factory(
                                activation=self._hybrid_activation,
                                model_adapter=model_adapter,
                                workspace_path=workspace_path,
                                **({"strict": True} if using_default_factory else {}),
                            )
                            if factory is not None
                            else None
                        )
                    except Exception as exc:
                        provider = None
                        initialization_reason = str(
                            getattr(exc, "reason", "provider_initialization_failed")
                        )[:96]
                    else:
                        initialization_reason = "provider_initialization_failed"
                    if provider is None:
                        self._hybrid_activation = replace(
                            self._hybrid_activation,
                            active=False,
                            reason=initialization_reason,
                        )
                    else:
                        self._retriever = CanonicalMemoryRetriever(
                            self._memory,
                            controller=self._injector._controller,
                            hybrid_provider=provider,
                        )
                        self._injector._retriever = self._retriever

        # Curator (background optimization)
        from minicode.memory_curator_agent import MemoryCuratorAgent
        self._curator = MemoryCuratorAgent(
            memory_manager=self._memory,
            model_adapter=model_adapter,
            workspace_path=workspace_path,
        )

        # Reflection engine (write path)
        if reflection_engine is not None:
            self._reflection = reflection_engine
        else:
            from minicode.agent_reflection import ReflectionEngine

            self._reflection = ReflectionEngine(memory_manager=None)

        self._initialized = True
        self._last_injected_ids: list[str] = []
        self._last_retrieval_result = None
        self._feedback_recorded = False
        logger.info("MemoryPipeline initialized: canonical_retrieval=true")

        # Restore persisted state
        self._load_state()

    # ── State persistence ────────────────────────────────────────────

    def _state_path(self) -> str | None:
        """Path for pipeline state file."""
        if not self._workspace:
            return None
        import os
        return os.path.join(self._workspace, ".mini-code-memory", "pipeline_state.json")

    def save_state(self) -> None:
        """Persist pipeline state to disk (cache stats, counters, curator history)."""
        path = self._state_path()
        if not path:
            return
        try:
            import json
            import os
            import tempfile

            target = Path(path)
            parent = target.parent
            if target.is_symlink() or parent.is_symlink():
                logger.warning(
                    "MemoryPipeline save_state skipped: unsafe symlink path %s",
                    target,
                )
                return
            os.makedirs(parent, exist_ok=True)
            state = {
                "read_count": self._read_count,
                "write_count": self._write_count,
                "maintain_count": self._maintain_count,
                "reranker_cache_hits": self._reranker._cache_hits if self._reranker else 0,
                "reranker_call_count": self._reranker._call_count if self._reranker else 0,
                "curator_history": self._curator.get_history() if self._curator else [],
                "timestamp": time.time(),
            }
            fd, tmp_path = tempfile.mkstemp(
                dir=str(parent),
                prefix=".pipeline_state.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
                os.replace(tmp_path, target)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.debug("MemoryPipeline save_state failed: %s", e)

    def _load_state(self) -> None:
        """Restore pipeline state from disk."""
        path = self._state_path()
        if not path:
            return
        try:
            import json
            import os
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._read_count = state.get("read_count", 0)
            self._write_count = state.get("write_count", 0)
            self._maintain_count = state.get("maintain_count", 0)
            if self._reranker:
                self._reranker._cache_hits = state.get("reranker_cache_hits", 0)
                self._reranker._call_count = state.get("reranker_call_count", 0)
            logger.debug("MemoryPipeline: restored state (%d reads, %d writes)",
                        self._read_count, self._write_count)
        except Exception as e:
            logger.debug("MemoryPipeline _load_state failed: %s", e)

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "read_count": self._read_count,
            "write_count": self._write_count,
            "maintain_count": self._maintain_count,
            "reranker_enabled": self._reranker.enabled if self._reranker else False,
            "reranker_cache_hit_rate": self._reranker.cache_hit_rate if self._reranker else 0.0,
            "vector_enabled": self._vector_store is not None and self._vector_store.enabled if self._vector_store else False,
            "hybrid_requested": bool(
                self._hybrid_activation and self._hybrid_activation.requested
            ),
            "hybrid_active": bool(
                self._hybrid_activation and self._hybrid_activation.active
            ),
            "hybrid_inactive_reason": (
                self._hybrid_activation.reason
                if self._hybrid_activation and not self._hybrid_activation.active
                else ""
            ),
            "hybrid_fallback": bool(
                self._hybrid_activation
                and self._hybrid_activation.requested
                and not self._hybrid_activation.active
            ),
            "hybrid_verifier_binding": self._hybrid_verifier_binding,
        }

    def _attach_hybrid_runtime_diagnostics(self, result: Any) -> Any:
        """Join activation state to a content-free retrieval observation."""
        from dataclasses import replace

        diagnostics = dict(getattr(result, "diagnostics", {}) or {})
        retrieval = diagnostics.get("hybrid", {})
        retrieval = retrieval if isinstance(retrieval, dict) else {}
        provider = retrieval.get("provider", {})
        provider = provider if isinstance(provider, dict) else {}
        requested = bool(
            self._hybrid_activation and self._hybrid_activation.requested
        )
        active = bool(self._hybrid_activation and self._hybrid_activation.active)
        adjudication_fallback = bool(retrieval.get("fallback"))
        reason = "not_requested"
        if requested and not active:
            reason = str(self._hybrid_activation.reason or "inactive")[:96]
        elif requested and adjudication_fallback:
            reason = str(retrieval.get("failure_reason") or "provider_fail_closed")[:96]
        elif requested:
            reason = "activated"
        diagnostics["hybrid_runtime"] = {
            "requested": requested,
            "active": active,
            "fallback": bool(requested and (not active or adjudication_fallback)),
            "reason": reason,
            "embedding_provider": str(
                getattr(self._hybrid_activation, "embedding_provider", "") or ""
            )[:32],
            "verifier_binding": self._hybrid_verifier_binding,
            "provider_cache_reused": bool(
                provider.get("provider_cache_reused", False)
            ),
            "adjudication_cache_hit": bool(provider.get("cache_hit", False)),
        }
        return replace(result, diagnostics=diagnostics)

    # ── READ: Memory retrieval ─────────────────────────────────────

    def read(
        self,
        task_description: str,
        current_files: list[str] | None = None,
        active_domains: list[str] | None = None,
        max_results: int = 15,
        *,
        max_total_tokens: int = 1200,
        max_tokens_per_memory: int | None = None,
        context_usage: float = 0.5,
        min_relevance: float | None = None,
        source_entrypoint: Any = None,
        recent_failure: bool = False,
        _record_retrieval: bool = True,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run canonical retrieval without modifying a prompt or recording injection."""
        if not self._memory or not self._retriever:
            self._last_retrieval_result = None
            return []

        self._read_count += 1
        from minicode.memory_retrieval import MemoryRetrievalRequest, RetrievalSource

        source = source_entrypoint or RetrievalSource.PIPELINE_READ
        per_memory = (
            max_tokens_per_memory
            if max_tokens_per_memory is not None
            else getattr(self._injector, "_max_tokens", 200)
        )
        relevance = (
            min_relevance
            if min_relevance is not None
            else getattr(self._injector, "_min_relevance", 0.0)
        )
        result = self._retriever.retrieve(
            MemoryRetrievalRequest(
                query=task_description,
                current_files=tuple(current_files or ()),
                active_domains=tuple(active_domains or ()),
                context_usage=context_usage,
                max_memories=max_results,
                max_total_tokens=max_total_tokens,
                max_tokens_per_memory=per_memory,
                min_relevance=relevance,
                source_entrypoint=source,
                recent_failure=recent_failure,
            ),
            agent_budget=agent_budget,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
            deadline_monotonic=deadline_monotonic,
        )
        result = self._attach_hybrid_runtime_diagnostics(result)
        self._last_retrieval_result = result
        if self._injector is not None:
            from minicode.memory_injector import (
                MemoryInjectionDecision,
                MemoryInjectionMode,
            )

            controller = result.controller_decision
            self._injector._last_decision = MemoryInjectionDecision(
                mode=MemoryInjectionMode(controller.get("mode", "none")),
                max_memories=int(controller.get("max_memories", 0)),
                min_relevance=float(controller.get("min_relevance", 1.0)),
                max_tokens_per_memory=int(controller.get("max_tokens_per_memory", 0)),
                reasons=list(controller.get("reasons", [])),
            )
        if (
            _record_retrieval
            and result.selected_ids
            and hasattr(self._memory, "record_retrievals")
        ):
            self._memory.record_retrievals(list(result.selected_ids))
        return [
            {
                "id": memory.entry_id,
                "content": memory.content,
                "domain": list(result.diagnostics.get("active_domains", [])),
                "relevance": memory.score.final_score,
                "score": memory.score.to_dict(),
                "scope": memory.scope,
                "category": memory.category,
                "source": memory.source,
                "reason_codes": list(memory.reason_codes),
            }
            for memory in result.rendered
        ]

    # ── INJECT: Memory into prompt ──────────────────────────────────

    def inject(
        self,
        task_description: str,
        current_files: list[str] | None,
        messages: list[dict],
        context_usage: float = 0.5,
        *,
        active_domains: list[str] | None = None,
        max_memories: int | None = None,
        max_total_tokens: int | None = None,
        max_tokens_per_memory: int | None = None,
        min_relevance: float | None = None,
        recent_failure: bool = False,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> list[dict]:
        """Read memories and inject into system prompt with adaptive cooldown.

        Adaptive cooldown (T1): τ_cool = τ_base × (1 - context_pressure).
        Returns modified messages with memory context appended to system message.
        """
        if not self._initialized:
            return messages
        self._last_injected_ids = []
        self._last_retrieval_result = None
        self._feedback_recorded = False

        # Project Facts are deterministic workspace inventory, not retrieved
        # lessons. They remain available when no MemoryManager exists and
        # while adaptive lesson injection is cooling down.
        facts_block = self._render_project_facts()

        def inject_facts_only() -> list[dict]:
            if not facts_block:
                return messages
            system_index = next(
                (
                    index
                    for index, message in enumerate(messages)
                    if message.get("role") == "system"
                ),
                None,
            )
            if system_index is None:
                return messages
            existing = str(messages[system_index].get("content", ""))
            if facts_block in existing:
                return messages
            messages[system_index] = {
                **messages[system_index],
                "content": existing + "\n" + facts_block,
            }
            return messages

        if not self._memory:
            return inject_facts_only()

        # Adaptive cooldown check
        now = time.time()
        cooldown = self._adaptive_cooldown(context_usage)
        if context_usage < 0.90 and hasattr(self, '_last_inject_time'):
            if now - self._last_inject_time < cooldown:
                return inject_facts_only()  # Lessons are still in cooldown.
        self._last_inject_time = now

        from minicode.memory_retrieval import (
            MEMORY_DIRECT_VERIFICATION_POLICY,
            RetrievalSource,
        )

        effective_max_memories = (
            max_memories
            if max_memories is not None
            else getattr(self._injector, "_max_injected", 5)
        )
        effective_per_memory = (
            max_tokens_per_memory
            if max_tokens_per_memory is not None
            else getattr(self._injector, "_max_tokens", 200)
        )
        effective_total_tokens = (
            max_total_tokens
            if max_total_tokens is not None
            else max(0, effective_max_memories * effective_per_memory + 64)
        )
        try:
            self.read(
                task_description,
                current_files=current_files,
                active_domains=active_domains,
                max_results=effective_max_memories,
                max_total_tokens=effective_total_tokens,
                max_tokens_per_memory=effective_per_memory,
                context_usage=context_usage,
                min_relevance=min_relevance,
                source_entrypoint=RetrievalSource.PIPELINE_INJECT,
                recent_failure=recent_failure,
                _record_retrieval=False,
                agent_budget=agent_budget,
                event_sink=event_sink,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            result = self._last_retrieval_result
            lesson_block = result.prompt_text if result is not None else ""
            if lesson_block:
                from minicode.context_manager import estimate_tokens

                policy_candidate = (
                    f"{lesson_block}\n\n{MEMORY_DIRECT_VERIFICATION_POLICY}"
                )
                if estimate_tokens(policy_candidate) <= effective_total_tokens:
                    lesson_block = policy_candidate
            prompt_blocks = [
                block for block in (lesson_block, facts_block) if block
            ]
            if not prompt_blocks:
                if result is not None and result.selected_ids:
                    self._memory.record_retrievals(list(result.selected_ids))
                return messages
            system_index = next(
                (index for index, message in enumerate(messages) if message.get("role") == "system"),
                None,
            )
            if system_index is None:
                if result is not None and result.selected_ids:
                    self._memory.record_retrievals(list(result.selected_ids))
                if result is not None:
                    self._last_retrieval_result = result.without_rendered(
                        "system_message_missing"
                    )
                return messages
            prompt_addition = "\n\n".join(prompt_blocks)
            messages[system_index] = {
                **messages[system_index],
                "content": str(messages[system_index].get("content", "")) + "\n" + prompt_addition,
            }
            if result is not None:
                self._last_injected_ids = list(result.rendered_ids)
                if lesson_block:
                    self._memory.record_retrievals_and_injections(
                        list(result.selected_ids),
                        self._last_injected_ids,
                    )
                elif result.selected_ids:
                    self._memory.record_retrievals(list(result.selected_ids))
            logger.info(
                "MemoryPipeline: injected %d memories, project_facts=%s (mode=%s)",
                len(self._last_injected_ids),
                bool(facts_block),
                (
                    result.controller_decision.get("mode", "none")
                    if result is not None
                    else "none"
                ),
            )
        except Exception as exc:
            from minicode.model_call_control import ModelCallDeadlineExceeded
            from minicode.turn_cancellation import TurnCancellationRequested

            if isinstance(
                exc,
                (TurnCancellationRequested, ModelCallDeadlineExceeded),
            ):
                raise
            logger.warning("MemoryPipeline injection failed safely: %s", exc)
            if self._last_retrieval_result is not None and not self._last_injected_ids:
                self._last_retrieval_result = self._last_retrieval_result.without_rendered(
                    "prompt_injection_failed"
                )

        return messages

    # ── WRITE: Memory persistence ──────────────────────────────────

    def write(
        self,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        *,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> str | None:
        """Write task reflection as structured memory.

        Uses ReflectionEngine to extract TaskContext with files, libraries,
        and domain tags. Returns the created memory entry ID or None.
        """
        if not self._reflection or not self._memory:
            self._last_written_ids = []
            return None

        self._write_count += 1
        self._last_written_ids = []

        result = None
        try:
            trace_safety = self._trace_memory_assessment(execution_trace)
            result = self._reflection.reflect(
                task_description,
                execution_trace,
                defer_shadow=True,
                agent_budget=agent_budget,
                event_sink=event_sink,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            value_decision = getattr(result, "value_decision", None) if result else None
            claim_validation = getattr(result, "claim_validation", None) if result else None
            valid_claims = getattr(claim_validation, "valid_claims", [])
            if not (
                result
                and value_decision is not None
                and value_decision.accepted
                and valid_claims
            ):
                reason_codes = getattr(value_decision, "reason_codes", ["legacy_result_default_denied"])
                logger.info(
                    "MemoryPipeline: skipped reflection write reasons=%s",
                    ",".join(str(code) for code in reason_codes[:8]),
                )
                fact_id = self._try_persist_conversation_fact(
                    task_description,
                    execution_trace,
                    result,
                    provenance=provenance,
                )
                if fact_id is not None:
                    self._last_written_ids = [fact_id]
                    self.save_state()
                    return fact_id
                self.save_state()
                return None
            if result:
                from dataclasses import replace as _replace

                from minicode.memory import (
                    MemoryApprovalPolicy,
                    MemoryScope,
                    MemoryTier,
                )

                # ── Layer 1: deterministic project facts ─────────────────
                # Dependencies are inventory, not lessons: they leave the
                # approval-bound memory store for the static facts store.
                self._persist_project_facts(
                    result,
                    task_description=task_description,
                    execution_trace=execution_trace,
                    provenance=provenance,
                )
                lesson_claims = [
                    claim
                    for claim in (getattr(result, "structured_claims", []) or [])
                    if claim.claim_type != "dependency"
                ]
                if not lesson_claims:
                    logger.info(
                        "MemoryPipeline: reflection carried only project facts; "
                        "no lesson entry queued for review"
                    )
                    fact_id = self._try_persist_conversation_fact(
                        task_description,
                        execution_trace,
                        result,
                        provenance=provenance,
                    )
                    if fact_id is not None:
                        self._last_written_ids = [fact_id]
                        self.save_state()
                        return fact_id
                    self.save_state()
                    return None
                if len(lesson_claims) != len(getattr(result, "structured_claims", []) or []):
                    result = _replace(result, structured_claims=lesson_claims)

                # ── Layer 2/3: one claim, one authority lifecycle ────────
                # Entry-level approval is safe only when every entry contains
                # one independently evidenced claim. This prevents a verified
                # recovery from auto-approving a weak/inferred neighbor.
                strong_signals = _STRONG_DURABLE_SIGNALS.intersection(
                    value_decision.durable_signals or []
                )
                safety_kwargs = {}
                if not trace_safety.allowed:
                    safety_kwargs = {
                        "safety_status": trace_safety.status,
                        "safety_reason": trace_safety.reason,
                    }
                written_ids: list[str] = []
                event_ids = [
                    str(event.get("event_id"))
                    for event in execution_trace
                    if isinstance(event, dict) and event.get("event_id")
                ][:64]
                for claim in lesson_claims:
                    existing_id, existing_rejected, same_statement = (
                        self._find_recurred_lesson(claim)
                    )
                    if existing_id is not None and same_statement:
                        if not existing_rejected:
                            self._memory.reinforce_reflection_entry(existing_id)
                        written_ids.append(existing_id)
                        continue

                    claim_has_own_strong_evidence = (
                        claim.epistemic_status == "confirmed"
                        and (
                            bool(claim.verification_ids)
                            or claim.claim_type in {"correction", "constraint"}
                        )
                    )
                    approval_policy = (
                        MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED
                        if strong_signals
                        and trace_safety.allowed
                        and claim_has_own_strong_evidence
                        else MemoryApprovalPolicy.USER_REVIEW_REQUIRED
                    )
                    single_result = _replace(
                        result,
                        structured_claims=[claim],
                        claim_validation=_replace(
                            result.claim_validation,
                            valid_claims=[claim],
                            rejected_claims=[],
                            issues=[
                                issue
                                for issue in result.claim_validation.issues
                                if issue.claim_id in {None, claim.claim_id}
                            ],
                        ),
                        value_decision=_replace(
                            value_decision,
                            accepted_claim_ids=[claim.claim_id],
                        ),
                    )
                    mem_data = single_result.to_memory_entry()
                    metadata = dict(mem_data.get("metadata", {}))
                    metadata["claim_identity"] = {
                        "claim_id": claim.claim_id,
                        "semantic_key": claim.semantic_key,
                    }
                    if existing_id is not None:
                        metadata["supersedes"] = [existing_id]
                    entry_provenance = {
                        **dict(provenance or {}),
                        "task": task_description[:300],
                        "trace_events": len(execution_trace),
                        "event_ids": event_ids,
                        "claim_id": claim.claim_id,
                        "semantic_key": claim.semantic_key,
                        "success": result.success,
                        "confidence": result.confidence,
                        "value_reason_codes": list(value_decision.reason_codes),
                        "durable_signals": list(value_decision.durable_signals),
                        "approval_basis": (
                            ",".join(sorted(str(s) for s in strong_signals))
                            if approval_policy
                            == MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED
                            else "user_review"
                        ),
                    }
                    entry = self._memory.add_entry(
                        scope=MemoryScope.PROJECT,
                        category=mem_data["category"],
                        content=mem_data["content"],
                        tags=mem_data["tags"],
                        domains=mem_data.get("domains", []),
                        metadata=metadata,
                        tier=MemoryTier.SHORT_TERM,
                        source="reflection",
                        provenance=entry_provenance,
                        approval_policy=approval_policy,
                        **safety_kwargs,
                    )
                    if entry is None:
                        continue
                    written_ids.append(entry.id)
                    if entry.is_active and existing_id is not None:
                        self._memory.apply_reflection_supersession(entry.id)
                if not written_ids:
                    self.save_state()
                    return None
                logger.info(
                    "MemoryPipeline: wrote/reinforced %d claim entries",
                    len(written_ids),
                )
                self._last_written_ids = list(dict.fromkeys(written_ids))
                self.save_state()
                return self._last_written_ids[0]
        except TurnCancellationRequested:
            raise
        except ModelCallDeadlineExceeded:
            logger.info("MemoryPipeline: reflection skipped at Agent deadline")
        except Exception as error:
            from minicode.memory import sanitize_for_persistence

            task_record = sanitize_for_persistence({"task": task_description})
            task_reference = (
                task_record.get("task", "[TASK_UNAVAILABLE]")
                if isinstance(task_record, dict)
                else "[TASK_UNAVAILABLE]"
            )
            # Do not attach ``exc_info`` here: a provider/tool exception may
            # quote the original task, defeating the content-free task handle.
            logger.error(
                "MemoryPipeline: reflection write failed task_ref=%s error_type=%s",
                task_reference,
                type(error).__name__,
            )
        finally:
            if result is not None and hasattr(self._reflection, "complete_shadow"):
                try:
                    self._reflection.complete_shadow(
                        result,
                        agent_budget=agent_budget,
                        event_sink=event_sink,
                        cancellation_token=cancellation_token,
                        deadline_monotonic=deadline_monotonic,
                    )
                except TurnCancellationRequested:
                    raise
                except ModelCallDeadlineExceeded:
                    logger.info(
                        "MemoryPipeline: shadow reflection stopped at Agent deadline"
                    )
                except Exception:
                    logger.info("MemoryPipeline: shadow comparison failed safely")

        self.save_state()
        return None

    @property
    def last_written_ids(self) -> tuple[str, ...]:
        """All entries produced or reinforced by the latest ``write`` call.

        ``write`` keeps its historical single-ID return value for callers that
        only need a representative entry. Run provenance must use this complete
        snapshot so a later user verdict reaches every independently persisted
        claim from the turn.
        """
        return tuple(self._last_written_ids)

    def _trace_memory_assessment(
        self, execution_trace: list[dict[str, Any]]
    ):
        """Conservatively scan trace text before automatic memory writes.

        Trace text is untrusted evidence. If it contains unsafe-looking
        instructions, the generated reflection is routed to pending approval
        rather than becoming injectable automatically.
        """
        return assess_trace_memory_safety(execution_trace)
    def _try_persist_conversation_fact(
        self,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        result: Any,
        *,
        provenance: dict[str, Any] | None = None,
    ) -> str | None:
        """Persist an ordinary user statement as a pending review candidate.

        This is the deterministic conversation-fact intake behind MEM-001.
        It is deliberately separate from reflection: no tool evidence, error,
        recovery or code decision is required. The candidate is always
        USER_REVIEW_REQUIRED, so a statement can never silently become
        injectable context.
        """
        fact = self._conversation_fact_text(task_description)
        if fact is None:
            return None
        if self._trace_has_technical_work(execution_trace, result):
            return None
        try:
            from minicode.memory import (
                MemoryApprovalPolicy,
                MemoryScope,
                MemoryTier,
            )

            entry = self._memory.add_entry(
                scope=MemoryScope.USER,
                category="conversation_fact",
                content=fact,
                tags=["conversation-fact", "user-statement"],
                domains=["memory"],
                tier=MemoryTier.SHORT_TERM,
                source="conversation_fact",
                provenance={
                    **dict(provenance or {}),
                    "task": task_description[:300],
                    "trace_events": len(execution_trace),
                    "event_ids": [
                        str(event.get("event_id"))
                        for event in execution_trace
                        if isinstance(event, dict) and event.get("event_id")
                    ][:64],
                    "intake": "deterministic_conversation_fact",
                    "approval_basis": "user_review",
                    "scope_basis": "personal_fact",
                },
                metadata={
                    "confidence": 0.6,
                    "evidence_kind": "user_statement",
                },
                approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
            )
            if entry is None:
                return None
            logger.info(
                "MemoryPipeline: queued conversation fact for review id=%s",
                entry.id,
            )
            return entry.id
        except Exception:
            logger.warning(
                "MemoryPipeline: conversation fact intake failed safely"
            )
            return None

    @staticmethod
    def _conversation_fact_text(task_description: str) -> str | None:
        import re

        text = str(task_description or "").strip()
        if not 2 <= len(text) <= 200:
            return None
        lowered = text.lower()
        if any(term in lowered for term in _CONVERSATION_FACT_TECHNICAL_TERMS):
            return None
        if re.search(r"(?:帮我|请|帮忙|修复|实现|添加|删除|创建|生成)", text):
            return None
        is_zh_fact = "我" in text and any(
            marker in text for marker in _CONVERSATION_FACT_ZH_MARKERS
        )
        is_en_fact = bool(
            re.search(
                r"(?i)(?:^|[.!?]\s*)(?:i am|i'm|my |our |i like|i prefer|"
                r"i use|i live in|i live|i work|i want|i need)\b",
                text,
            )
        )
        if not (is_zh_fact or is_en_fact):
            return None
        return text.rstrip("。.!！?？ ")

    @staticmethod
    def _trace_has_technical_work(
        execution_trace: list[dict[str, Any]],
        result: Any,
    ) -> bool:
        evidence = getattr(result, "task_evidence", None) if result else None
        if evidence is not None:
            if any(
                (
                    getattr(evidence, "tool_calls", []),
                    getattr(evidence, "files_read", []),
                    getattr(evidence, "files_changed", []),
                    getattr(evidence, "referenced_files", []),
                    getattr(evidence, "errors", []),
                    getattr(evidence, "recoveries", []),
                    getattr(evidence, "decisions", []),
                    getattr(evidence, "libraries", []),
                    getattr(evidence, "verification", []),
                )
            ):
                return True
            return False
        for event in execution_trace or []:
            if not isinstance(event, dict):
                continue
            if event.get("type") in {
                "tool_call",
                "tool_result",
                "error",
                "recovery",
                "decision",
            }:
                return True
            if event.get("tool_name"):
                return True
        return False


    def _persist_project_facts(
        self,
        result: Any,
        *,
        task_description: str,
        execution_trace: list[dict[str, Any]],
        provenance: dict[str, Any] | None,
    ) -> None:
        """Move confirmed dependency names into the static facts store."""
        if self._project_facts is None or not bool(getattr(result, "success", False)):
            return
        claims = getattr(result, "structured_claims", []) or []
        if not any(claim.claim_type == "dependency" for claim in claims):
            return
        evidence = getattr(result, "task_evidence", None)
        names = [
            item.name
            for item in (getattr(evidence, "libraries", []) or [])
            if getattr(item, "status", "") == "confirmed"
        ]
        if not names:
            return
        evidence_by_name = {
            item.name: list(getattr(item, "event_ids", ()) or ())
            for item in (getattr(evidence, "libraries", []) or [])
            if getattr(item, "status", "") == "confirmed"
        }
        try:
            added = self._project_facts.observe_dependencies(
                names,
                provenance={
                    **dict(provenance or {}),
                    "source": "reflection_evidence",
                    "task": task_description[:300],
                    "outcome": "success" if result.success else "failed",
                    "event_ids": [
                        str(event.get("event_id"))
                        for event in execution_trace
                        if isinstance(event, dict) and event.get("event_id")
                    ][:64],
                    "dependency_evidence": evidence_by_name,
                },
            )
            if added:
                logger.info(
                    "MemoryPipeline: recorded %d project fact(s): %s",
                    added,
                    ", ".join(sorted(names)[:6]),
                )
        except Exception:  # noqa: BLE001 - facts are advisory
            logger.warning("MemoryPipeline: project facts write failed safely")

    def _find_recurred_lesson(
        self, claim: Any
    ) -> tuple[str | None, bool, bool]:
        """Find an existing claim with the same semantic identity.

        Returns ``(entry_id, rejected, same_statement)``. A same-key changed
        statement is a correction candidate, not recurrence.
        """
        semantic_key = str(getattr(claim, "semantic_key", "") or "")
        statement = str(getattr(claim, "statement", "") or "").strip()
        if not semantic_key or not self._memory:
            return None, False, False
        from minicode.memory import MemoryScope

        try:
            entries = self._memory.memories[MemoryScope.PROJECT].entries
        except Exception:  # noqa: BLE001
            return None, False, False
        for entry in entries:
            metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
            reflection = metadata.get("structured_reflection")
            if not isinstance(reflection, dict):
                continue
            claims = reflection.get("claims")
            if not isinstance(claims, list):
                continue
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                if claim.get("semantic_key") == semantic_key:
                    rejected = (
                        getattr(entry, "approval_status", "") == "rejected"
                        or getattr(entry, "lifecycle_status", "") == "rejected"
                    )
                    same_statement = str(claim.get("statement", "")).strip() == statement
                    return entry.id, rejected, same_statement
        return None, False, False

    def _render_project_facts(self) -> str:
        """Bounded prompt block of deterministic project facts."""
        if self._project_facts is None:
            return ""
        try:
            return self._project_facts.render_markdown()
        except Exception:  # noqa: BLE001
            return ""

    def _trace_contains_unsafe_memory_payload(
        self, execution_trace: list[dict[str, Any]]
    ) -> bool:
        """Backward-compatible boolean wrapper for older tests/callers."""
        return not self._trace_memory_assessment(execution_trace).allowed

    # ── FEEDBACK: Close the quality loop (F2) ────────────────────────

    def feedback(
        self,
        task_success: bool | str,
        injected_memory_ids: list[str] | None = None,
        *,
        verification_passed: int = 0,
        verification_failed: int = 0,
        verification_memory_ids: list[str] | None = None,
        observation_id: str | None = None,
    ) -> None:
        """Task outcome → memory utility. Closes the outermost learning loop.

        Success → boost injected memories (positive reinforcement).
        Failure → gentle decay (they may have misled the agent).

        ``verification_passed``/``verification_failed`` are an optional tally
        of independently executed test/build/lint/typecheck outcomes observed
        during this same turn. Counts alone do not identify which rendered
        Memory the verifier exercised. Corroborated feedback therefore also
        requires ``verification_memory_ids``: an exact, trusted binding to a
        subset of this turn's rendered IDs. The canonical Agent path currently
        omits that binding and records only ordinary whole-turn feedback rather
        than risking causal misattribution.
        """
        if not self._memory or self._feedback_recorded:
            return
        rendered_ids = list(self._last_injected_ids)
        if not rendered_ids:
            return
        if injected_memory_ids is not None and list(dict.fromkeys(injected_memory_ids)) != rendered_ids:
            logger.warning("Memory feedback rejected IDs outside this turn's rendered result")
            return
        if isinstance(task_success, str):
            normalized = task_success.strip().lower()
            if normalized == "success":
                success: bool | None = True
            elif normalized in {"failed", "failure"}:
                success = False
            else:
                logger.info("Memory feedback skipped because turn outcome is unknown")
                return
        else:
            success = bool(task_success)
        if hasattr(self._memory, "record_feedback"):
            if observation_id is None:
                # Preserve compatibility with evaluator/test managers that
                # implement the legacy two-argument feedback seam.
                self._memory.record_feedback(rendered_ids, success)
            else:
                self._memory.record_feedback(
                    rendered_ids,
                    success,
                    observation_id=observation_id,
                )
            self._feedback_recorded = True
            corroborated = verification_corroboration(
                verification_passed, verification_failed
            )
            bound_ids: list[str] = []
            if verification_memory_ids is not None:
                if not isinstance(verification_memory_ids, list) or any(
                    not isinstance(entry_id, str)
                    for entry_id in verification_memory_ids
                ):
                    logger.warning(
                        "Memory corroborated feedback rejected malformed verification binding"
                    )
                else:
                    candidate_ids = list(dict.fromkeys(verification_memory_ids))
                    if candidate_ids and all(
                        entry_id in rendered_ids for entry_id in candidate_ids
                    ):
                        bound_ids = candidate_ids
                    elif candidate_ids:
                        logger.warning(
                            "Memory corroborated feedback rejected IDs outside this turn's rendered result"
                        )
            if corroborated is not None and bound_ids and not (
                isinstance(observation_id, str) and observation_id.strip()
            ):
                logger.warning(
                    "Memory corroborated feedback rejected missing observation identity"
                )
            elif (
                corroborated is not None
                and bound_ids
                and hasattr(self._memory, "record_corroborated_feedback")
            ):
                self._memory.record_corroborated_feedback(
                    bound_ids,
                    corroborated,
                    source="independent_verification",
                    observation_id=observation_id,
                )

    @property
    def last_retrieval_result(self):
        """The exact result whose rendered IDs belong to the current turn."""
        return self._last_retrieval_result

    # ── MAINTAIN: Background optimization ───────────────────────────

    def maintain(
        self,
        force: bool = False,
        *,
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any] | None:
        """Run background memory optimization.

        Consolidates insights, archives duplicates, validates against codebase,
        promotes/demotes tiers, and links related memories.

        Returns CuratorReport as dict, or None if not ready.
        """
        if not self._curator:
            return None

        self._curator.on_task_complete()

        if not force and not self._curator.should_run:
            return None

        self._maintain_count += 1
        try:
            report = self._curator.run_cycle(
                force=True,
                agent_budget=agent_budget,
                event_sink=event_sink,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            self.save_state()
            return report.to_dict()
        except TurnCancellationRequested:
            raise
        except Exception:
            return None

    # ── Internal ────────────────────────────────────────────────────

    def _get_active_domains(
        self, current_files: list[str], task_description: str
    ) -> list[str]:
        try:
            from minicode.domain_classifier import get_active_domain_values
            return get_active_domain_values(
                current_files=current_files,
                intent_text=task_description,
            )
        except Exception:
            return []

    # ── T1: Memory Value Function + Adaptive Cooldown ───────────────

    # Formal definition:
    #   V(m, t, c) = relevance(m, t) × freshness(m) × utility(m, c)
    #   where:
    #     relevance(m, t) = BM25_score(m, t) ∈ [0, 1]
    #     freshness(m)    = exp(-age_days / τ) with τ = 30 days
    #     utility(m, c)   = 1 + α × I(m was used in similar context c)
    #
    # Adaptive cooldown:  τ_cool(c) = τ_base × (1 - context_pressure)
    #   High context pressure → shorter cooldown → faster injection
    #   Low context pressure → longer cooldown → less noise

    _TAU_FRESHNESS = 30.0  # days
    _ALPHA_UTILITY = 0.15

    def _memory_value(
        self, bm25_score: float, entry: Any, context_usage: float = 0.5
    ) -> float:
        """Compute V(m, t, c) for a single memory entry."""
        import math
        age_days = (time.time() - getattr(entry, 'updated_at', time.time())) / 86400.0
        freshness = math.exp(-age_days / self._TAU_FRESHNESS)
        utility = 1.0 + self._ALPHA_UTILITY * math.log1p(getattr(entry, 'usage_count', 0))
        return bm25_score * freshness * utility

    def _adaptive_cooldown(self, context_usage: float) -> float:
        """Compute adaptive injection cooldown based on context pressure.

        τ_cool = τ_base × (1 - context_pressure), clamped to [5s, 120s].
        High pressure → shorter cooldown (memory is more needed).
        """
        base = 30.0  # seconds
        return max(5.0, min(120.0, base * (1.0 - context_usage)))

    # ── T2: Query Reformulation ─────────────────────────────────────

    # When BM25 returns poor results (top score < τ_low), attempt
    # reformulation: strip stopwords, try domain synonyms, expand abbreviations.
    # Max 3 attempts. If no improvement, keep original results.

    _QUERY_STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                        "to", "of", "in", "for", "on", "with", "at", "by", "from",
                        "and", "or", "but", "not", "this", "that", "it", "i", "we",
                        "add", "create", "make", "implement", "build", "set", "get"}

    _QUERY_REFORMULATIONS = [
        lambda q: " ".join(w for w in q.lower().split() if w not in MemoryPipeline._QUERY_STOPWORDS),
        lambda q: q.lower().replace("  ", " ").strip(),
    ]

    def _reformulate_query(self, query: str) -> list[str]:
        """Generate reformulated query variants."""
        variants = [query]
        for reformulate in self._QUERY_REFORMULATIONS:
            v = reformulate(query)
            if v and v != query and v not in variants:
                variants.append(v)
        return variants[:3]

    def _try_search_with_reformulation(
        self, task_description: str, active_domains: list[str] | None, max_results: int
    ) -> list[Any]:
        """Search with query reformulation fallback for poor initial results."""

        entries = self._memory.search(
            task_description, limit=max_results, active_domains=active_domains,
        )

        if entries and len(entries) >= 3:
            return entries  # Good enough

        # Try reformulations
        for variant in self._reformulate_query(task_description):
            if variant == task_description:
                continue
            alt = self._memory.search(
                variant, limit=max_results, active_domains=active_domains,
            )
            if len(alt) > len(entries):
                logger.debug("Query reformulation improved: %d → %d results", len(entries), len(alt))
                return alt

        return entries

    # ── T3: Spreading Activation ────────────────────────────────────

    # When memory m is retrieved, its related_to neighbors also receive
    # activation: score_neighbor += score(m) × decay × sim(m, neighbor)
    # depth=1, decay=0.5. This surfaces related memories the user might
    # not have explicitly searched for.

    _SPREAD_DECAY = 0.5
    _SPREAD_THRESHOLD = 0.3

    def _spread_activation(
        self, entries: list[Any]
    ) -> list[Any]:
        """Enrich results via spreading activation through related_to graph.

        Concatenates directly-linked neighbors with decayed relevance.
        """
        if not self._memory or not entries:
            return entries

        seen_ids = {e.id for e in entries}
        neighbors = []

        for entry in entries[:5]:  # Only spread from top 5
            if not hasattr(entry, 'related_to') or not entry.related_to:
                continue
            for rid in entry.related_to:
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                # Find neighbor in memory
                for scope_name in ["project", "local", "user"]:
                    try:
                        from minicode.memory import MemoryScope
                        scope = MemoryScope(scope_name)
                        if scope in self._memory.memories:
                            self._memory.memories[scope]._ensure_cache_valid()
                            nbr = self._memory.memories[scope]._id_index.get(rid)
                            if nbr and getattr(nbr, "is_active", True):
                                neighbors.append(nbr)
                                break
                    except (ValueError, KeyError):
                        continue

        return entries + neighbors
