from __future__ import annotations

import inspect
import math
from pathlib import Path

import pytest

from minicode.context_manager import estimate_tokens
from minicode.memory import MemoryEntry, MemoryManager, MemoryScope, MemoryTier
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_injector import MemoryInjector
from minicode.memory_retrieval import (
    CanonicalMemoryRetriever,
    MemoryRetrievalRequest,
    RetrievalScore,
    RetrievalSource,
)


def _manager(tmp_path: Path, entries: list[MemoryEntry]) -> MemoryManager:
    manager = MemoryManager(project_root=tmp_path)
    for entry in entries:
        manager.memories[entry.scope].entries.append(entry)
    for memory_file in manager.memories.values():
        memory_file._rebuild_indices()
    return manager


def _entry(
    entry_id: str,
    content: str,
    *,
    scope: MemoryScope = MemoryScope.PROJECT,
    category: str = "architecture",
    tags: list[str] | None = None,
    domains: list[str] | None = None,
    tier: MemoryTier = MemoryTier.SHORT_TERM,
    lifecycle_status: str = "active",
    approval_status: str = "approved",
    safety_status: str = "safe",
    curator_locked: bool = False,
    updated_at: float = 1_700_000_000.0,
    usefulness_score: float = 0.0,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        scope=scope,
        category=category,
        content=content,
        tags=list(tags or []),
        domains=list(domains or []),
        tier=tier,
        lifecycle_status=lifecycle_status,
        approval_status=approval_status,
        safety_status=safety_status,
        curator_locked=curator_locked,
        created_at=updated_at,
        updated_at=updated_at,
        last_accessed=updated_at,
        usefulness_score=usefulness_score,
    )


def _request(query: str, **overrides: object) -> MemoryRetrievalRequest:
    values: dict[str, object] = {
        "query": query,
        "current_files": (),
        "active_domains": (),
        "context_usage": 0.4,
        "max_memories": 5,
        "max_total_tokens": 800,
        "max_tokens_per_memory": 160,
        "min_relevance": 0.0,
        "source_entrypoint": RetrievalSource.CANONICAL,
    }
    values.update(overrides)
    return MemoryRetrievalRequest(**values)


def test_request_normalizes_query_and_copies_collections() -> None:
    files = [" src/a.py ", "src/a.py", "src/b.py"]
    domains = [" Backend ", "backend", "security"]

    request = _request(
        "  Fix\n  API\t timeout  ",
        current_files=files,
        active_domains=domains,
    )
    files.append("src/c.py")
    domains.clear()

    assert request.query == "Fix API timeout"
    assert request.current_files == ("src/a.py", "src/b.py")
    assert request.active_domains == ("backend", "security")


@pytest.mark.parametrize("value", [-1, -100])
@pytest.mark.parametrize(
    "field",
    ["max_memories", "max_total_tokens", "max_tokens_per_memory"],
)
def test_request_rejects_negative_limits(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        _request("database migration", **{field: value})


@pytest.mark.parametrize(("value", "expected"), [(-0.5, 0.0), (0.4, 0.4), (1.7, 1.0)])
def test_request_clamps_context_usage(value: float, expected: float) -> None:
    assert _request("database migration", context_usage=value).context_usage == expected


def test_request_rejects_unknown_entrypoint() -> None:
    with pytest.raises(ValueError, match="source_entrypoint"):
        _request("database migration", source_entrypoint="unknown")


def test_score_requires_finite_components() -> None:
    with pytest.raises(ValueError, match="finite"):
        RetrievalScore(lexical_score=math.nan, final_score=math.inf)


def test_queryless_production_request_fails_closed(tmp_path: Path) -> None:
    entry = _entry("queryless", "Database migration uses concurrent index creation.")
    manager = _manager(tmp_path, [entry])

    result = CanonicalMemoryRetriever(manager).retrieve(_request(" \n\t "))

    assert result.no_match is True
    assert result.no_match_reason == "queryless_production_request"
    assert result.candidate_ids == ()
    assert result.selected_ids == ()
    assert result.rendered_ids == ()
    assert entry.retrieval_count == 0
    assert entry.injection_count == 0


def test_weak_common_token_overlap_returns_no_match(tmp_path: Path) -> None:
    manager = _manager(
        tmp_path,
        [
            _entry("weak-a", "The old implementation of billing uses an event stream."),
            _entry(
                "weak-b",
                "New files end with a newline.",
                scope=MemoryScope.USER,
                category="convention",
            ),
        ],
    )

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("Make a new implementation for an unrelated feature")
    )

    assert result.no_match is True
    assert result.rendered_ids == ()
    assert set(result.suppressed_ids) == {"weak-a", "weak-b"}


def test_exact_tag_phrase_is_strong_evidence(tmp_path: Path) -> None:
    entry = _entry(
        "tag-match",
        "Use bounded attempts for transient failures.",
        tags=["invoice-retry-policy"],
    )
    manager = _manager(tmp_path, [entry])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("invoice retry policy")
    )

    assert result.rendered_ids == (entry.id,)
    assert "exact_tag_match" in result.rendered[0].reason_codes


def test_file_basename_is_strong_evidence(tmp_path: Path) -> None:
    entry = _entry(
        "file-match",
        "CheckoutForm.tsx displays validation errors only after blur.",
        tags=["CheckoutForm.tsx"],
    )
    manager = _manager(tmp_path, [entry])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("Fix validation behavior", current_files=["web/CheckoutForm.tsx"])
    )

    assert result.rendered_ids == (entry.id,)
    assert result.rendered[0].score.file_score > 0
    assert "file_match" in result.rendered[0].reason_codes


def test_intent_only_domain_classification_is_diagnostic_not_a_gate(tmp_path: Path) -> None:
    relevant = _entry(
        "intent-domain",
        "API request cache keys include the normalized path.",
        domains=["backend"],
    )
    unrelated = _entry(
        "domain-only",
        "Queue workers use bounded concurrency.",
        domains=["backend"],
    )
    manager = _manager(tmp_path, [relevant, unrelated])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("Implement an API request cache")
    )

    assert result.diagnostics["domain_source"] == "intent_derived"
    assert relevant.id in result.rendered_ids
    assert unrelated.id not in result.selected_ids


def test_cross_scope_sorting_keeps_strong_project_above_weak_authority_matches(
    tmp_path: Path,
) -> None:
    strong = _entry(
        "strong-project",
        "Exact payment webhook replay deduplication uses the provider event ID.",
        scope=MemoryScope.PROJECT,
        tags=["payment", "webhook", "replay", "deduplication"],
        domains=["backend"],
    )
    weak_user = _entry(
        "weak-user",
        "Prefer payment examples in documentation.",
        scope=MemoryScope.USER,
        category="preference",
        tags=["payment"],
        domains=["documentation"],
        updated_at=1_900_000_000.0,
        usefulness_score=1.0,
    )
    weak_local = _entry(
        "weak-local",
        "Webhook examples use short names.",
        scope=MemoryScope.LOCAL,
        category="convention",
        tags=["webhook"],
        domains=["documentation"],
        updated_at=1_900_000_000.0,
        usefulness_score=1.0,
    )
    manager = _manager(tmp_path, [weak_user, weak_local, strong])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request(
            "Fix exact payment webhook replay deduplication",
            active_domains=["backend"],
        )
    )

    assert result.rendered_ids[0] == strong.id
    if weak_user.id in result.rendered_ids:
        assert result.rendered_ids.index(strong.id) < result.rendered_ids.index(weak_user.id)
    assert weak_local.id not in result.selected_ids


def test_recency_and_usefulness_cannot_activate_unrelated_memory(tmp_path: Path) -> None:
    unrelated = _entry(
        "high-utility-noise",
        "Frontend avatar colors follow the theme.",
        scope=MemoryScope.USER,
        updated_at=9_999_999_999.0,
        usefulness_score=1.0,
    )
    manager = _manager(tmp_path, [unrelated])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("Repair database migration transaction retry")
    )

    assert result.rendered_ids == ()


@pytest.mark.parametrize(
    ("overrides", "entry_id"),
    [
        ({"lifecycle_status": "pending"}, "pending"),
        ({"lifecycle_status": "rejected"}, "rejected"),
        ({"lifecycle_status": "deprecated"}, "deprecated"),
        ({"tier": MemoryTier.ARCHIVAL}, "archival"),
        ({"curator_locked": True}, "locked"),
        ({"approval_status": "pending"}, "approval-pending"),
        ({"approval_status": "rejected"}, "approval-rejected"),
        ({"safety_status": "unsafe"}, "unsafe"),
    ],
)
def test_inactive_states_are_excluded_before_candidate_evaluation(
    tmp_path: Path,
    overrides: dict[str, object],
    entry_id: str,
) -> None:
    entry = _entry(
        entry_id,
        "Invoice retry policy uses three bounded attempts.",
        tags=["invoice", "retry", "policy"],
        **overrides,
    )
    manager = _manager(tmp_path, [entry])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("invoice retry policy")
    )

    assert entry.id not in result.candidate_ids
    assert entry.id not in result.rendered_ids


def test_sort_order_is_deterministic_with_explicit_tie_breakers(tmp_path: Path) -> None:
    entries = [
        _entry(
            entry_id,
            "Webhook retry status applies only to transient failures.",
            scope=scope,
            tags=["webhook", "retry", "status"],
            domains=["backend"],
        )
        for entry_id, scope in [
            ("z-local", MemoryScope.LOCAL),
            ("a-project", MemoryScope.PROJECT),
            ("m-user", MemoryScope.USER),
        ]
    ]
    manager = _manager(tmp_path, list(reversed(entries)))
    retriever = CanonicalMemoryRetriever(manager)
    request = _request("webhook retry status", active_domains=["backend"])

    first = retriever.retrieve(request).rendered_ids
    second = retriever.retrieve(request).rendered_ids

    assert first == second


def test_max_memories_is_a_final_hard_limit(tmp_path: Path) -> None:
    entries = [
        _entry(
            f"limit-{index}",
            f"Search indexing reliability checkpoint rule number {index}.",
            tags=["search", "indexing", "reliability"],
        )
        for index in range(4)
    ]
    manager = _manager(tmp_path, entries)

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("search indexing reliability", max_memories=1)
    )

    assert len(result.rendered_ids) == 1
    assert len(result.rendered_ids) <= result.controller_decision["max_memories"]


def test_total_and_per_memory_token_budgets_include_formatting(tmp_path: Path) -> None:
    entry = _entry(
        "token-budget",
        "Database migration guidance " + "transaction rollback " * 120,
        tags=["database", "migration", "transaction"],
    )
    manager = _manager(tmp_path, [entry])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request(
            "database migration transaction",
            max_total_tokens=80,
            max_tokens_per_memory=24,
        )
    )

    assert result.total_tokens == estimate_tokens(result.prompt_text)
    assert result.total_tokens <= 80
    assert result.rendered[0].token_count <= 24
    assert result.rendered[0].truncated is True


def test_oversized_first_candidate_does_not_block_shorter_second(tmp_path: Path) -> None:
    oversized = _entry(
        "oversized",
        "Exact settlement checksum mismatch " + "background notes " * 300,
        scope=MemoryScope.LOCAL,
        tags=["settlement", "checksum", "mismatch"],
    )
    compact = _entry(
        "compact",
        "Exact settlement checksum mismatch is fixed by decoding once before hashing.",
        scope=MemoryScope.PROJECT,
        tags=["settlement", "checksum", "mismatch"],
    )
    manager = _manager(tmp_path, [oversized, compact])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request(
            "fix exact settlement checksum mismatch",
            max_total_tokens=45,
            max_tokens_per_memory=200,
        )
    )

    assert compact.id in result.rendered_ids
    assert result.total_tokens <= 45


@pytest.mark.parametrize("context_usage", [0.90, 0.95, 1.0])
def test_critical_context_pressure_renders_nothing(
    tmp_path: Path,
    context_usage: float,
) -> None:
    entry = _entry(
        "pressure",
        "Database transaction retry handles serialization failures.",
        tags=["database", "transaction", "retry"],
    )
    manager = _manager(tmp_path, [entry])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("database transaction retry", context_usage=context_usage)
    )

    assert result.rendered_ids == ()
    assert result.controller_decision["mode"] == "none"


def test_summary_mode_obeys_count_and_token_budgets(tmp_path: Path) -> None:
    entries = [
        _entry(
            f"summary-{index}",
            "Database migration transaction retry " + "bounded rollback " * 40,
            tags=["database", "migration", "transaction"],
        )
        for index in range(4)
    ]
    manager = _manager(tmp_path, entries)

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request(
            "database migration transaction retry",
            context_usage=0.80,
            max_memories=5,
            max_total_tokens=120,
            max_tokens_per_memory=80,
        )
    )

    assert result.controller_decision["mode"] == "summary"
    assert len(result.rendered_ids) <= 2
    assert result.total_tokens <= 120


def test_pipeline_read_and_inject_share_result_and_true_relevance(tmp_path: Path) -> None:
    entry = _entry(
        "pipeline-entry",
        "Checkout total rounding occurs once after summing decimal line amounts.",
        tags=["checkout", "total", "rounding"],
        domains=["backend"],
    )
    manager = _manager(tmp_path, [entry])
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(tmp_path), enable_reranker=False)

    read = pipeline.read(
        "fix checkout total rounding",
        active_domains=["backend"],
        max_results=5,
    )
    messages = pipeline.inject(
        "fix checkout total rounding",
        [],
        [{"role": "system", "content": "SYSTEM"}],
        context_usage=0.4,
    )

    assert read[0]["id"] == entry.id
    assert read[0]["relevance"] == pytest.approx(read[0]["score"]["final_score"])
    assert pipeline._last_injected_ids == [entry.id]
    assert messages[0]["content"].count(entry.content) == 1


def test_prompt_append_failure_records_no_injection(tmp_path: Path) -> None:
    entry = _entry(
        "no-system",
        "Checkout total rounding occurs once after summing decimal line amounts.",
        tags=["checkout", "total", "rounding"],
    )
    manager = _manager(tmp_path, [entry])
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(tmp_path), enable_reranker=False)

    pipeline.inject(
        "fix checkout total rounding",
        [],
        [{"role": "user", "content": "fix it"}],
        context_usage=0.4,
    )

    assert pipeline._last_injected_ids == []
    assert entry.injection_count == 0
    assert pipeline.last_retrieval_result is not None
    assert pipeline.last_retrieval_result.rendered_ids == ()


def test_only_rendered_ids_receive_injection_and_feedback(tmp_path: Path) -> None:
    entries = [
        _entry(
            f"feedback-{index}",
            f"Report export timeout rule {index} moves generation to a bounded job.",
            tags=["report", "export", "timeout"],
        )
        for index in range(3)
    ]
    manager = _manager(tmp_path, entries)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(tmp_path), enable_reranker=False)
    pipeline._injector._max_injected = 1

    pipeline.inject(
        "fix report export timeout",
        [],
        [{"role": "system", "content": "SYSTEM"}],
        context_usage=0.4,
    )
    rendered = set(pipeline._last_injected_ids)
    pipeline.feedback("success")
    pipeline.feedback("success")

    assert len(rendered) == 1
    for entry in entries:
        expected = 1 if entry.id in rendered else 0
        assert entry.injection_count == expected
        assert entry.success_count == expected


def test_feedback_rejects_caller_supplied_non_rendered_ids(tmp_path: Path) -> None:
    rendered = _entry(
        "rendered",
        "Report export timeout moves generation to a bounded job.",
        tags=["report", "export", "timeout"],
    )
    unrelated = _entry("unrelated", "Avatar colors follow the current theme.")
    manager = _manager(tmp_path, [rendered, unrelated])
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(tmp_path), enable_reranker=False)
    pipeline._injector._max_injected = 1
    pipeline.inject(
        "fix report export timeout",
        [],
        [{"role": "system", "content": "SYSTEM"}],
        context_usage=0.4,
    )

    pipeline.feedback(True, [unrelated.id])

    assert unrelated.success_count == 0
    assert rendered.success_count == 0


@pytest.mark.parametrize(
    ("outcome", "success", "failure"),
    [("success", 1, 0), ("failed", 0, 1), ("unknown", 0, 0)],
)
def test_feedback_uses_final_turn_outcome(
    tmp_path: Path,
    outcome: str,
    success: int,
    failure: int,
) -> None:
    entry = _entry(
        f"outcome-{outcome}",
        "Package download timeout recovered after one bounded retry.",
        tags=["package", "download", "timeout", "recovered"],
    )
    manager = _manager(tmp_path, [entry])
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(tmp_path), enable_reranker=False)
    pipeline.inject(
        "recover package download timeout",
        [],
        [{"role": "system", "content": "SYSTEM"}],
        context_usage=0.4,
    )

    pipeline.feedback(outcome)

    assert entry.success_count == success
    assert entry.failure_count == failure


def test_feedback_and_counts_survive_reload(tmp_path: Path) -> None:
    entry = _entry(
        "reload-counts",
        "Database migration retry handles serialization failures.",
        tags=["database", "migration", "retry"],
    )
    manager = _manager(tmp_path, [entry])
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(tmp_path), enable_reranker=False)
    pipeline.inject(
        "database migration retry",
        [],
        [{"role": "system", "content": "SYSTEM"}],
        context_usage=0.4,
    )
    pipeline.feedback("success")

    reloaded = MemoryManager(project_root=tmp_path)
    loaded = reloaded._find_entry_by_id(entry.id)[1]

    assert loaded is not None
    assert loaded.retrieval_count == 1
    assert loaded.injection_count == 1
    assert loaded.success_count == 1


def test_pipeline_default_path_never_enables_remote_reranker(tmp_path: Path) -> None:
    manager = _manager(tmp_path, [])
    pipeline = MemoryPipeline(manager)

    pipeline.initialize(model_adapter=object(), workspace_path=str(tmp_path))

    assert pipeline._reranker is None
    assert pipeline.stats["reranker_enabled"] is False


def test_queryless_compatibility_injection_fails_closed_unless_management_is_explicit(
    tmp_path: Path,
) -> None:
    from minicode.memory import inject_memory_into_prompt

    entry = _entry("compat", "Database migration uses concurrent index creation.")
    manager = _manager(tmp_path, [entry])

    assert inject_memory_into_prompt("SYSTEM", manager) == "SYSTEM"
    managed = inject_memory_into_prompt("SYSTEM", manager, management_mode=True)

    assert entry.content in managed
    assert entry.retrieval_count == 0
    assert entry.injection_count == 0


def test_query_aware_compatibility_injection_batches_task_start_counters(
    tmp_path: Path,
) -> None:
    from minicode.memory import inject_memory_into_prompt

    entry = _entry(
        "compat-query",
        "Database migration retry handles serialization failures.",
        tags=["database", "migration", "retry"],
    )
    manager = _manager(tmp_path, [entry])
    saved_scopes: list[MemoryScope] = []
    manager._save_scope = saved_scopes.append  # type: ignore[method-assign]

    prompt = inject_memory_into_prompt(
        "SYSTEM",
        manager,
        query="database migration retry",
    )

    assert entry.content in prompt
    assert entry.retrieval_count == 1
    assert entry.injection_count == 1
    assert saved_scopes == [MemoryScope.PROJECT]


def test_manager_context_and_pipeline_read_have_the_same_top_result(tmp_path: Path) -> None:
    primary = _entry(
        "agreement-primary",
        "Checkout total rounding occurs once after summing decimal line amounts.",
        tags=["checkout", "total", "rounding"],
        domains=["backend"],
    )
    noise = _entry(
        "agreement-noise",
        "Checkout headings use sentence case.",
        scope=MemoryScope.USER,
        category="style",
        tags=["checkout"],
        domains=["frontend"],
    )
    manager = _manager(tmp_path, [noise, primary])
    query = "fix checkout total rounding"

    context = manager.get_relevant_context(query=query)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(tmp_path), enable_reranker=False)
    read = pipeline.read(query, active_domains=["backend"])

    assert primary.content in context
    assert read[0]["id"] == primary.id
    assert noise.content not in context


def test_injector_cache_and_cooldown_never_record_unrendered_injections(
    tmp_path: Path,
) -> None:
    entry = _entry(
        "injector-cache",
        "Database migration retry handles serialization failures.",
        tags=["database", "migration", "retry"],
    )
    manager = _manager(tmp_path, [entry])
    injector = MemoryInjector(manager, injection_cooldown=0.0)
    first = injector.inject_for_task("database migration retry")
    cached = injector.inject_for_task("database migration retry")

    assert [item.entry_id for item in first] == [entry.id]
    assert [item.entry_id for item in cached] == [entry.id]
    assert entry.injection_count == 0

    cooldown = MemoryInjector(manager, injection_cooldown=60.0)
    assert cooldown.inject_for_task("database migration retry")
    assert cooldown.inject_for_task("database migration retry") == []
    assert entry.injection_count == 0


def test_injector_has_no_legacy_search_or_relevance_implementation(tmp_path: Path) -> None:
    manager = _manager(tmp_path, [])
    injector = MemoryInjector(manager)

    assert not hasattr(injector, "_calculate_relevance")
    assert not hasattr(injector, "_inject_by_tags")


def test_injector_ignores_free_text_reranker_summary(tmp_path: Path) -> None:
    class FakeReranker:
        enabled = True

        def __init__(self) -> None:
            self.calls = 0

        def curate(self, *args: object, **kwargs: object) -> object:
            self.calls += 1
            raise AssertionError("Phase 2A must not call the reranker")

    entry = _entry(
        "no-summary",
        "Database migration retry handles serialization failures.",
        tags=["database", "migration", "retry"],
    )
    manager = _manager(tmp_path, [entry])
    reranker = FakeReranker()
    injector = MemoryInjector(manager, reranker=reranker)

    injected = injector.inject_for_task("database migration retry")

    assert reranker.calls == 0
    assert [item.entry_id for item in injected] == [entry.id]
    assert all("Curator Summary" not in item.content for item in injected)


def test_context_compactor_uses_latest_user_query_and_fails_closed_without_one() -> None:
    from minicode.context_compactor import SessionMemoryCompactEngine

    class QuerySpyMemory:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def get_relevant_context(self, *, query: str, max_tokens: int) -> str:
            self.queries.append(query)
            return ""

    memory = QuerySpyMemory()
    engine = SessionMemoryCompactEngine(memory_manager=memory)
    messages = [
        {"role": "user", "content": "old database task"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "latest checkout rounding task"},
        {"role": "assistant", "content": "working"},
    ]

    assert engine.try_session_memory_compact(messages, context_window=10_000) is None
    assert memory.queries == ["latest checkout rounding task"]

    memory.queries.clear()
    assert engine.try_session_memory_compact(
        [{"role": "system", "content": "SYSTEM"}],
        context_window=10_000,
    ) is None
    assert memory.queries == []


def test_agent_turn_performs_one_main_retrieval_and_one_prompt_injection(
    tmp_path: Path,
) -> None:
    from minicode.agent_loop import run_agent_turn
    from minicode.mock_model import MockModelAdapter
    from minicode.tooling import ToolRegistry

    entry = _entry(
        "agent-owner",
        "Checkout total rounding occurs once after summing decimal line amounts.",
        tags=["checkout", "total", "rounding"],
        domains=["backend"],
    )
    manager = _manager(tmp_path, [entry])
    search_calls: list[str] = []
    original_search = manager.search

    def observed_search(query: str, *args: object, **kwargs: object):
        search_calls.append(query)
        return original_search(query, *args, **kwargs)

    manager.search = observed_search  # type: ignore[method-assign]
    result = run_agent_turn(
        model=MockModelAdapter(),
        tools=ToolRegistry([]),
        messages=[
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "fix checkout total rounding"},
        ],
        cwd=str(tmp_path),
        memory_manager=manager,
        enable_work_chain=True,
        max_steps=2,
    )

    system = next(message["content"] for message in result if message["role"] == "system")
    assert search_calls == ["fix checkout total rounding"]
    assert system.count(entry.content) == 1
    assert entry.retrieval_count == 1
    assert entry.injection_count == 1
    # Completion without an independent verifier is deliberately not treated as
    # evidence that the retrieved memory helped produce a correct answer.
    assert entry.success_count == 0
    assert entry.failure_count == 0


def test_recovered_tool_error_does_not_reward_memory_without_verification(
    tmp_path: Path,
) -> None:
    from minicode.agent_loop import run_agent_turn
    from minicode.mock_model import MockModelAdapter
    from minicode.permissions import PermissionManager
    from minicode.tools import create_default_tool_registry

    entry = _entry(
        "recovered-success",
        "cmd false failure recovery is verified after correcting the shell command.",
        category="failure-recovery",
        tags=["cmd-false"],
    )
    manager = _manager(tmp_path, [entry])
    tools = create_default_tool_registry(str(tmp_path), runtime=None)
    permissions = PermissionManager(
        str(tmp_path),
        prompt=lambda _request: {"decision": "allow_once"},
    )
    try:
        run_agent_turn(
            model=MockModelAdapter(),
            tools=tools,
            messages=[
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "/cmd false"},
            ],
            cwd=str(tmp_path),
            permissions=permissions,
            memory_manager=manager,
            enable_work_chain=True,
            max_steps=3,
        )
    finally:
        tools.dispose()

    assert entry.injection_count == 1
    assert entry.success_count == 0
    assert entry.failure_count == 0


def test_main_tui_and_headless_delegate_persistent_memory_to_agent_pipeline() -> None:
    from minicode import headless, main
    from minicode.agent_loop import run_agent_turn
    from minicode.tui import input_handler

    for entrypoint in (main.main, headless.run_headless, input_handler._handle_input):
        source = inspect.getsource(entrypoint)
        assert "get_relevant_context" not in source
        assert "memory_manager=memory_mgr" in source

    agent_source = inspect.getsource(run_agent_turn)
    assert "MemoryManager(" not in agent_source
