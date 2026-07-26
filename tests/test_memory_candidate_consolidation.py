from __future__ import annotations

from dataclasses import replace

from minicode.memory import MemoryEntry, MemoryScope
from minicode.memory_candidate_consolidation import (
    CandidateConsolidator,
    SuppressionReason,
    extract_candidate_signals,
)
from minicode.memory_retrieval import (
    MemoryRetrievalRequest,
    RetrievedMemory,
    RetrievalScore,
)


def _candidate(
    entry_id: str,
    content: str,
    *,
    matched: tuple[str, ...],
    scope: str = "project",
    category: str = "architecture",
    domains: tuple[str, ...] = ("backend",),
    tags: tuple[str, ...] = (),
    authority: tuple[str, ...] = (),
    relations: tuple[tuple[str, str], ...] = (),
    file_score: float = 0.0,
    domain_score: float = 1.0,
    rank: int = 1,
    score: float = 0.9,
) -> RetrievedMemory:
    return RetrievedMemory(
        entry_id=entry_id,
        scope=scope,
        category=category,
        content=content,
        score=RetrievalScore(
            lexical_score=score,
            file_score=file_score,
            domain_score=domain_score,
            final_score=score,
            matched_terms=matched,
        ),
        rank=rank,
        token_count=20,
        truncated=False,
        source=f"{scope}_memory",
        tags=tags,
        domains=domains,
        authority_signals=authority,
        relations=relations,
    )


def _request(
    query: str,
    *,
    files: tuple[str, ...] = (),
    domains: tuple[str, ...] = ("backend",),
) -> MemoryRetrievalRequest:
    return MemoryRetrievalRequest(
        query=query,
        current_files=files,
        active_domains=domains,
    )


def _suppression(result, entry_id: str):
    return next(item for item in result.suppressed if item.entry_id == entry_id)


def test_specific_project_fact_suppresses_weak_user_documentation_preference() -> None:
    primary = _candidate(
        "project-fact",
        "Payment webhook replay deduplication uses the provider event ID.",
        matched=("payment", "webhook", "replay", "deduplication"),
        tags=("payment", "webhook", "replay", "deduplication"),
    )
    preference = _candidate(
        "user-preference",
        "Prefer payment examples in documentation.",
        matched=("payment",),
        scope="user",
        category="preference",
        domains=("documentation",),
        domain_score=0.0,
        rank=2,
        score=0.5,
    )

    result = CandidateConsolidator().consolidate(
        (primary, preference),
        _request("Fix payment webhook replay deduplication"),
    )

    assert result.retained_ids == (primary.entry_id,)
    suppression = _suppression(result, preference.entry_id)
    assert suppression.reason == SuppressionReason.NO_INCREMENTAL_VALUE
    assert suppression.dominating_candidate_id == primary.entry_id


def test_file_bound_snapshot_rule_suppresses_other_object_collision() -> None:
    primary = _candidate(
        "snapshot-file",
        "test_nav_snapshot.py snapshots normalize generated element IDs.",
        matched=("snapshot",),
        category="testing",
        domains=("testing", "frontend"),
        tags=("snapshot", "test_nav_snapshot.py"),
        file_score=1.0,
    )
    collision = _candidate(
        "api-snapshot",
        "API contract snapshots omit request timestamps.",
        matched=("snapshot",),
        category="api",
        domains=("backend", "testing"),
        rank=2,
        score=0.6,
    )

    result = CandidateConsolidator().consolidate(
        (primary, collision),
        _request(
            "Update snapshot expectations",
            files=("tests/ui/test_nav_snapshot.py",),
            domains=("testing", "frontend"),
        ),
    )

    assert result.retained_ids == (primary.entry_id,)
    assert _suppression(result, collision.entry_id).reason == SuppressionReason.NO_INCREMENTAL_VALUE


def test_api_cache_rule_suppresses_hostname_documentation_noise() -> None:
    primary = _candidate(
        "api-cache",
        "API request cache keys include method, path, and authorization scope.",
        matched=("api", "request", "cache"),
        category="performance",
    )
    noise = _candidate(
        "api-docs",
        "API request examples use placeholder hostnames.",
        matched=("api", "request"),
        scope="user",
        category="documentation",
        domains=("documentation",),
        domain_score=0.0,
        rank=2,
        score=0.55,
    )

    result = CandidateConsolidator().consolidate(
        (primary, noise),
        _request("Implement an API request cache"),
    )

    assert result.retained_ids == (primary.entry_id,)


def test_canonical_bounded_retry_suppresses_infinite_retry_conflict() -> None:
    canonical = _candidate(
        "bounded-retry",
        "Canonical notification retry sequence waits with jitter and retries once.",
        matched=("notification", "retry", "sequence"),
        category="failure-recovery",
        authority=("canonical",),
    )
    conflict = _candidate(
        "infinite-retry",
        "Notification retry sequence repeats forever without delay.",
        matched=("notification", "retry", "sequence"),
        scope="user",
        category="failure-recovery",
        authority=("unverified",),
        rank=2,
        score=0.8,
    )

    result = CandidateConsolidator().consolidate(
        (canonical, conflict),
        _request("Use the canonical notification retry sequence"),
    )

    suppression = _suppression(result, conflict.entry_id)
    assert result.retained_ids == (canonical.entry_id,)
    assert suppression.reason == SuppressionReason.LOWER_AUTHORITY_CONFLICT
    assert suppression.dominating_candidate_id == canonical.entry_id


def test_verified_recovery_suppresses_same_chain_unverified_recovery() -> None:
    verified = _candidate(
        "verified",
        "Verified SQLite lock recovery closes the leaked fixture connection.",
        matched=("sqlite", "lock", "recovery"),
        category="failure-recovery",
        authority=("verified",),
    )
    unverified = _candidate(
        "unverified",
        "Possible SQLite lock recovery might add a long sleep; not verified.",
        matched=("sqlite", "lock", "recovery"),
        category="failure-recovery",
        authority=("unverified",),
        rank=2,
        score=0.8,
    )

    result = CandidateConsolidator().consolidate(
        (verified, unverified),
        _request("Recover a SQLite lock failure"),
    )

    assert result.retained_ids == (verified.entry_id,)
    assert _suppression(result, unverified.entry_id).reason == SuppressionReason.UNVERIFIED_RECOVERY


def test_user_correction_suppresses_disproved_old_decision() -> None:
    correction = _candidate(
        "correction",
        "User correction: webhook retry applies only to 429 and 5xx statuses.",
        matched=("webhook", "retry", "status"),
        category="correction",
        authority=("user_correction",),
    )
    old = _candidate(
        "old",
        "Webhook retry applies to every non-success status.",
        matched=("webhook", "retry", "status"),
        category="decision",
        rank=2,
        score=0.8,
    )

    result = CandidateConsolidator().consolidate(
        (correction, old),
        _request("Use the corrected webhook retry status set"),
    )

    assert result.retained_ids == (correction.entry_id,)
    assert _suppression(result, old.entry_id).reason == SuppressionReason.LOWER_AUTHORITY_CONFLICT


def test_complementary_verification_method_is_retained() -> None:
    primary = _candidate(
        "cache-policy",
        "Canonical account cache policy keys by account ID and tenant ID.",
        matched=("account", "cache", "policy"),
        authority=("canonical",),
    )
    verification = _candidate(
        "cache-verification",
        "Verify account cache isolation with a cross-tenant regression test.",
        matched=("account", "cache"),
        category="testing",
        authority=("verified",),
        rank=2,
        score=0.78,
    )

    result = CandidateConsolidator().consolidate(
        (primary, verification),
        _request("Apply account cache policy"),
    )

    assert result.retained_ids == (primary.entry_id, verification.entry_id)
    assert result.suppressed_ids == ()


def test_same_chain_authority_order_precedes_hard_budget_ordering() -> None:
    verification = _candidate(
        "cache-verification",
        "Verify checkout cache isolation with a cross-account regression test.",
        matched=("checkout", "cache", "isolation"),
        category="testing",
        authority=("verified",),
        score=0.92,
    )
    canonical = _candidate(
        "cache-policy",
        "Canonical checkout cache isolation keys by account ID and cart ID.",
        matched=("checkout", "cache", "isolation"),
        authority=("canonical",),
        rank=2,
        score=0.90,
    )

    result = CandidateConsolidator().consolidate(
        (verification, canonical),
        _request("Apply checkout cache isolation"),
    )

    assert result.retained_ids == (canonical.entry_id, verification.entry_id)
    assert result.suppressed_ids == ()


def test_structured_near_duplicate_accepts_conservative_paraphrase_overlap() -> None:
    canonical = _candidate(
        "rounding-policy",
        "Canonical verified order total rounding occurs once after summing decimal line amounts.",
        matched=("order", "total", "rounding"),
        category="decision",
        authority=("canonical", "verified"),
    )
    copy = _candidate(
        "rounding-copy",
        "Order totals are rounded one time after decimal line amounts are summed.",
        matched=("order", "total", "rounding"),
        category="decision",
        rank=2,
        score=0.86,
    )

    result = CandidateConsolidator().consolidate(
        (canonical, copy),
        _request("Apply order total rounding policy"),
    )

    assert result.retained_ids == (canonical.entry_id,)
    assert _suppression(result, copy.entry_id).reason == SuppressionReason.NEAR_DUPLICATE


def test_candidates_sharing_only_generic_terms_are_not_joined_or_suppressed() -> None:
    first = _candidate(
        "api-test",
        "Billing API contract test checks decimal totals.",
        matched=("api", "test"),
        category="testing",
    )
    second = _candidate(
        "other-api-test",
        "Profile API load test checks avatar throughput.",
        matched=("api", "test"),
        category="performance",
        rank=2,
        score=0.8,
    )

    result = CandidateConsolidator().consolidate(
        (first, second),
        _request("Review API test coverage"),
    )

    assert result.retained_ids == (first.entry_id, second.entry_id)


def test_cross_scope_stable_user_testing_constraint_is_retained() -> None:
    primary = _candidate(
        "race-fix",
        "Inventory reservation race is prevented by a conditional update.",
        matched=("inventory", "reservation", "race"),
    )
    user_constraint = _candidate(
        "race-tests",
        "Race tests must use fixed seeds.",
        matched=("race",),
        scope="user",
        category="testing",
        domains=("testing",),
        domain_score=0.0,
        rank=2,
        score=0.6,
    )

    result = CandidateConsolidator().consolidate(
        (primary, user_constraint),
        _request("Resolve inventory reservation race condition", domains=("backend", "database")),
    )

    assert result.retained_ids == (primary.entry_id, user_constraint.entry_id)


def test_equal_authority_explicit_conflict_fails_closed_for_both_candidates() -> None:
    first = _candidate(
        "mode-a",
        "Widget mode must be enabled for batch imports.",
        matched=("widget", "mode", "batch", "import"),
        relations=(("conflicts_with", "mode-b"),),
    )
    second = _candidate(
        "mode-b",
        "Widget mode must be disabled for batch imports.",
        matched=("widget", "mode", "batch", "import"),
        relations=(("conflicts_with", "mode-a"),),
        rank=2,
        score=0.9,
    )

    result = CandidateConsolidator().consolidate(
        (first, second),
        _request("Choose widget mode for batch imports"),
    )

    assert result.retained_ids == ()
    assert result.suppressed_ids == (first.entry_id, second.entry_id)
    assert all(
        item.reason == SuppressionReason.UNRESOLVED_CONFLICT
        for item in result.suppressed
    )


def test_structured_canonical_near_duplicate_suppresses_lower_authority_copy() -> None:
    canonical = _candidate(
        "canonical-copy",
        "Catalog lookup uses normalized SKU before fuzzy title matching.",
        matched=("catalog", "lookup", "sku"),
        authority=("canonical", "verified"),
    )
    duplicate = _candidate(
        "draft-copy",
        "Catalog lookups use a normalized SKU before fuzzy matching on title.",
        matched=("catalog", "lookup", "sku"),
        rank=2,
        score=0.86,
    )

    result = CandidateConsolidator().consolidate(
        (canonical, duplicate),
        _request("Fix catalog lookup by SKU"),
    )

    assert result.retained_ids == (canonical.entry_id,)
    assert _suppression(result, duplicate.entry_id).reason == SuppressionReason.NEAR_DUPLICATE


def test_distinct_file_and_error_objects_do_not_cross_chain() -> None:
    type_error = _candidate(
        "client-typeerror",
        "client.py TypeError is fixed by converting Decimal before formatting.",
        matched=("failure",),
        category="failure-recovery",
        tags=("typeerror", "client.py"),
        file_score=1.0,
    )
    timeout = _candidate(
        "worker-timeout",
        "worker.py timeout is fixed by acknowledging the lease after flush.",
        matched=("failure",),
        category="failure-recovery",
        tags=("timeout", "worker.py"),
        file_score=1.0,
        rank=2,
        score=0.8,
    )

    result = CandidateConsolidator().consolidate(
        (type_error, timeout),
        _request(
            "Review failure recovery",
            files=("src/client.py", "src/worker.py"),
        ),
    )

    assert result.retained_ids == (type_error.entry_id, timeout.entry_id)


def test_same_basename_in_distinct_directories_does_not_establish_a_chain() -> None:
    canonical = _candidate(
        "service-a-config",
        "service_a/config.py retry mode uses a bounded queue.",
        matched=("config",),
        authority=("canonical",),
        file_score=1.0,
    )
    independent = _candidate(
        "service-b-config",
        "service_b/config.py retry mode uses a bounded queue.",
        matched=("config",),
        file_score=1.0,
        rank=2,
        score=0.8,
    )

    result = CandidateConsolidator().consolidate(
        (canonical, independent),
        _request(
            "Review config",
            files=("service_a/config.py", "service_b/config.py"),
        ),
    )

    assert result.retained_ids == (canonical.entry_id, independent.entry_id)
    assert result.suppressed_ids == ()


def test_consolidation_does_not_mutate_input_candidates() -> None:
    first = _candidate("first", "Canonical retry runs once.", matched=("retry",), authority=("canonical",))
    second = _candidate("second", "Retry runs forever.", matched=("retry",), rank=2)
    before = (first, second)

    CandidateConsolidator().consolidate(before, _request("Apply retry policy"))

    assert before == (first, second)


def test_signal_extraction_bounds_cyclic_metadata_and_provenance() -> None:
    metadata: dict[str, object] = {"canonical": True, "supersedes": ["old-entry"]}
    metadata["self"] = metadata
    provenance: dict[str, object] = {"verified": True}
    provenance["cycle"] = provenance
    entry = MemoryEntry(
        id="cyclic-entry",
        scope=MemoryScope.PROJECT,
        category="decision",
        content="Current retry policy is verified.",
        metadata=metadata,
        provenance=provenance,
    )

    signals = extract_candidate_signals(entry)

    assert "canonical" in signals.authority_signals
    assert "verified" in signals.authority_signals
    assert ("supersedes", "old-entry") in signals.relations
    assert signals.visited_nodes <= signals.node_limit


def test_verification_instruction_is_not_verified_authority() -> None:
    entry = MemoryEntry(
        id="verification-instruction",
        scope=MemoryScope.PROJECT,
        category="testing",
        content="Verify report export recovery with an interrupted pagination test.",
    )

    signals = extract_candidate_signals(entry)

    assert "verified" not in signals.authority_signals


def test_canonical_noun_inside_testing_content_is_not_authority() -> None:
    entry = MemoryEntry(
        id="canonical-noun",
        scope=MemoryScope.PROJECT,
        category="testing",
        content="Serialization tests compare canonical dictionaries.",
    )

    signals = extract_candidate_signals(entry)

    assert "canonical" not in signals.authority_signals


def test_candidate_cap_is_deterministic_and_marks_overflow() -> None:
    candidates = tuple(
        replace(
            _candidate(
                f"candidate-{index:04d}",
                f"Independent component {index} uses setting {index}.",
                matched=(f"component-{index}",),
                rank=index + 1,
                score=1.0 - index / 2000,
            ),
            updated_at=float(1000 - index),
        )
        for index in range(1000)
    )
    consolidator = CandidateConsolidator(max_candidates=256)

    first = consolidator.consolidate(candidates, _request("Review component settings"))
    second = consolidator.consolidate(candidates, _request("Review component settings"))

    assert first == second
    assert len(first.retained) <= 256
    assert len(first.suppressed) >= 744
    assert all(
        item.reason == SuppressionReason.CANDIDATE_LIMIT
        for item in first.suppressed
        if item.entry_id >= "candidate-0256"
    )
