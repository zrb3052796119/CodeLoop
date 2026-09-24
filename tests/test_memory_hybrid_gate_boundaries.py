from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from minicode.memory import MemoryEntry, MemoryManager, MemoryScope
from minicode.memory_hybrid_runtime import HybridRuntimeProvider
from minicode.memory_retrieval import CanonicalMemoryRetriever, MemoryRetrievalRequest
from minicode.types import AgentStep, StepDiagnostics


class _ConstantEncoder:
    def encode_documents(self, texts):
        return tuple((1.0, 0.0) for _ in texts)

    def encode_queries(self, texts):
        return tuple((1.0, 0.0) for _ in texts)


def _completed_step(payload: dict[str, object]) -> AgentStep:
    return AgentStep(
        type="assistant",
        content=json.dumps(payload),
        diagnostics=StepDiagnostics(stopReason="stop", blockTypes=["text"]),
    )


def _memory(
    entry_id: str,
    content: str,
    *,
    metadata: dict[str, object] | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        scope=MemoryScope.PROJECT,
        category="architecture",
        content=content,
        metadata=dict(metadata or {}),
        approval_status="approved",
        lifecycle_status="active",
        safety_status="safe",
    )


def _request(query: str, *current_files: str) -> SimpleNamespace:
    return SimpleNamespace(
        query=query,
        current_files=current_files,
        active_domains=(),
    )


class _SemanticModel:
    """Return one consistent verifier/challenger policy and record each stage."""

    model_id = "synthetic-boundary-model"

    def __init__(self, *, relevant: bool) -> None:
        self.relevant = relevant
        self.call_kinds: list[str] = []

    def next(self, messages):
        system = messages[0]["content"]
        pairs = json.loads(messages[1]["content"])["pairs"]
        if "admission auditor" in system:
            self.call_kinds.append("challenger")
            return _completed_step(
                {
                    "audits": [
                        {
                            "id": pair["id"],
                            "admit": True,
                            "confidence": 0.99,
                            "reasonCode": "no_disqualifier",
                        }
                        for pair in pairs
                    ]
                }
            )

        self.call_kinds.append("verifier")
        if self.relevant:
            decisions = [
                {
                    "id": pair["id"],
                    "decision": "relevant",
                    "confidence": 0.99,
                    "objectMatch": True,
                    "relationSupported": True,
                    "reasonCode": "constraint",
                }
                for pair in pairs
            ]
        else:
            decisions = [
                {
                    "id": pair["id"],
                    "decision": "irrelevant",
                    "confidence": 0.99,
                    "objectMatch": False,
                    "relationSupported": False,
                    "reasonCode": "different_object",
                }
                for pair in pairs
            ]
        return _completed_step({"decisions": decisions})


class _ExactPathOnlyResolver:
    """Claim relevance using only a shared path, never query-task evidence."""

    model_id = "synthetic-exact-path-resolver"

    def __init__(self, *, query_kind: str = "query.current_files") -> None:
        self.call_kinds: list[str] = []
        self.query_kind = query_kind

    def next(self, messages):
        system = messages[0]["content"]
        pairs = json.loads(messages[1]["content"])["pairs"]
        if "disagreement resolver" in system:
            self.call_kinds.append("resolver")
            resolutions = []
            for pair in pairs:
                query_evidence_id = next(
                    segment["id"]
                    for segment in pair["queryEvidenceSegments"]
                    if segment["kind"] == self.query_kind
                )
                memory_evidence_id = next(
                    segment["id"]
                    for segment in pair["memoryEvidenceSegments"]
                    if segment["kind"] == "memory.content"
                )
                resolutions.append(
                    {
                        "id": pair["id"],
                        "decision": "relevant",
                        "confidence": 0.99,
                        "queryGrounded": True,
                        "objectMatch": True,
                        "relationSupported": True,
                        "supportKind": "exact_file_context",
                        "disqualifier": "none",
                        "queryEvidenceId": query_evidence_id,
                        "memoryEvidenceId": memory_evidence_id,
                    }
                )
            return _completed_step({"resolutions": resolutions})

        if "admission auditor" in system:
            self.call_kinds.append("challenger")
            return _completed_step(
                {
                    "audits": [
                        {
                            "id": pair["id"],
                            "admit": False,
                            "confidence": 0.99,
                            "reasonCode": "different_object",
                        }
                        for pair in pairs
                    ]
                }
            )

        self.call_kinds.append("verifier")
        return _completed_step(
            {
                "decisions": [
                    {
                        "id": pair["id"],
                        "decision": "relevant",
                        "confidence": 0.99,
                        "objectMatch": True,
                        "relationSupported": True,
                        "reasonCode": "semantic_equivalence",
                    }
                    for pair in pairs
                ]
            }
        )


def _provider(model, *, resolver: bool = False) -> HybridRuntimeProvider:
    return HybridRuntimeProvider(
        encoder=_ConstantEncoder(),
        model_adapter=model,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        challenger_minimum_confidence=0.8,
        resolver_minimum_confidence=0.9 if resolver else None,
        embedding_provider="qwen",
    )


@pytest.mark.parametrize(
    ("case_name", "query", "content"),
    (
        (
            "english_generic_anchors",
            "Change the retry handler policy in services/api/config.py.",
            (
                "Migration m-1 moved the logging handler policy to "
                "services/api/config.py."
            ),
        ),
        (
            "chinese_generic_anchors",
            "修改 services/api/config.py 的重试配置处理。",
            "迁移 m-1 将日志配置处理移动到 services/api/config.py。",
        ),
    ),
)
def test_generic_rename_anchors_do_not_bypass_semantic_gate(
    case_name: str,
    query: str,
    content: str,
) -> None:
    entry = _memory(
        f"rename-{case_name}",
        content,
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-1",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(query, "services/api/config.py"),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


@pytest.mark.parametrize(
    ("case_name", "query", "content", "forbidden_reason"),
    (
        (
            "order_marker_in_other_clause",
            "Refresh the cache before publishing the receipt.",
            (
                "Refresh the cache after startup, then publish the receipt after "
                "refreshing the cache."
            ),
            "deterministic_operation_order_conflict",
        ),
        (
            "public_marker_for_other_service",
            "Bind the admin service to localhost.",
            (
                "Bind the admin service to localhost; expose the public "
                "documentation on all interfaces."
            ),
            "deterministic_network_exposure_conflict",
        ),
        (
            "no_confirmation_for_other_action",
            "Archive deletion must ask for confirmation.",
            (
                "Archive deletion must ask for confirmation; formatting comments "
                "needs no confirmation."
            ),
            "deterministic_confirmation_policy_conflict",
        ),
    ),
)
def test_multiclause_markers_do_not_trigger_deterministic_rejection(
    case_name: str,
    query: str,
    content: str,
    forbidden_reason: str,
) -> None:
    entry = _memory(f"multiclause-{case_name}", content)
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request(query),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert forbidden_reason not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier", "challenger"]


@pytest.mark.parametrize(
    ("query", "content"),
    (
        (
            "Start auth before database migration.",
            "Start database migration after auth.",
        ),
        (
            "Run cache cleanup after worker shutdown.",
            "Run worker shutdown before cache cleanup.",
        ),
        (
            "Upload manifest before deployment.",
            "Deployment happens after manifest upload.",
        ),
    ),
)
def test_equivalent_before_after_inversion_is_not_a_conflict(
    query: str,
    content: str,
) -> None:
    entry = _memory("equivalent-order-edge", content)
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request(query),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_operation_order_conflict" not in (
        result.signals[0].reason_codes
    )
    assert model.call_kinds == ["verifier", "challenger"]


def test_exact_file_context_cannot_resolve_a_different_operation() -> None:
    entry = _memory(
        "same-file-different-operation",
        "services/api/config.py enables exponential retry backoff for failed uploads.",
        metadata={"path": "services/api/config.py"},
    )
    model = _ExactPathOnlyResolver()

    result = _provider(model, resolver=True).adjudicate(
        request=_request("Format comments only.", "services/api/config.py"),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_exact_file_context_also_requires_shared_task_operation() -> None:
    entry = _memory(
        "same-file-different-task-evidence",
        "services/api/config.py enables exponential retry backoff for failed uploads.",
        metadata={"path": "services/api/config.py"},
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request("Format comments only.", "services/api/config.py"),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_explicit_old_to_new_rename_remains_deterministically_accepted() -> None:
    entry = _memory(
        "explicit-path-pair",
        "Migration m-2 moved ownership from legacy/api/settings.py to "
        "services/api/config.py.",
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-2",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(
            "Apply the rename from legacy/api/settings.py to "
            "services/api/config.py.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_proven_rename" in result.signals[0].reason_codes
    assert model.call_kinds == []


def test_quoted_and_parenthesized_rename_keeps_exact_direction_proof() -> None:
    entry = _memory(
        "wrapped-explicit-path-pair",
        "Migration m-2w moved ownership from (legacy/api/settings.py) to "
        "(services/api/config.py).",
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-2w",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(
            "Apply the rename from `legacy/api/settings.py` to "
            "`services/api/config.py`.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_proven_rename" in result.signals[0].reason_codes
    assert model.call_kinds == []


@pytest.mark.parametrize(
    "qualification",
    (
        "only in staging",
        "unless compatibility mode is active",
        "for tenant alpha only",
        "仅在预发环境",
    ),
)
def test_qualified_rename_never_becomes_a_global_deterministic_fact(
    qualification: str,
) -> None:
    entry = _memory(
        "qualified-path-pair",
        "Migration m-q moved ownership from legacy/api/settings.py to "
        "services/api/config.py.",
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-q",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(
            "Apply the rename from legacy/api/settings.py to "
            f"services/api/config.py {qualification}.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


def test_multivalue_rename_metadata_cannot_form_a_cartesian_proof() -> None:
    entry = _memory(
        "multi-edge-path-pair",
        "Migration m-many moved ownership from legacy/billing/settings.py to "
        "services/billing/config.py.",
        metadata={
            "renamed_from": [
                "legacy/api/settings.py",
                "legacy/billing/settings.py",
            ],
            "renamed_to": [
                "services/api/config.py",
                "services/billing/config.py",
            ],
            "migration_id": "m-many",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(
            "Apply the rename from legacy/api/settings.py to "
            "services/api/config.py.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


@pytest.mark.parametrize(
    "malformed_destination",
    (
        "services/api/config.py.bak",
        "services/api/config.py\u034f",
        "services/api/config.py\u200dbackup",
        "services/api/config.py#backup",
        "services/api/config.py:suffix",
        "services/API/Config.py",
    ),
)
def test_malformed_or_case_only_rename_path_cannot_borrow_current_file_authority(
    malformed_destination: str,
) -> None:
    entry = _memory(
        "malformed-query-path-pair",
        "Migration m-edge moved ownership from legacy/api/settings.py to "
        "services/api/config.py.",
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-edge",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(
            "Apply the rename from legacy/api/settings.py to "
            f"{malformed_destination}.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


@pytest.mark.parametrize(
    "content",
    (
        "Migration m-11 did not move ownership from "
        "legacy/api/settings.py to services/api/config.py.",
        "Migration m-11 moved ownership from services/api/config.py to "
        "legacy/api/settings.py.",
        "Migration m-11 moved ownership from unrelated/other.py to "
        "services/api/config.py.",
        "Migration m-11 is failing to move ownership from "
        "legacy/api/settings.py to services/api/config.py.",
        "Migration m-11 was unable to move ownership from "
        "legacy/api/settings.py to services/api/config.py.",
        "Migration m-11 is pending to move ownership from "
        "legacy/api/settings.py to services/api/config.py.",
        "Migration m-11 is scheduled to move ownership from "
        "legacy/api/settings.py to services/api/config.py.",
        "迁移 m-11 并未将 legacy/api/settings.py 移动到 "
        "services/api/config.py。",
    ),
)
def test_rename_fast_path_requires_an_affirmative_matching_memory_claim(
    content: str,
) -> None:
    entry = _memory(
        "unproven-memory-rename",
        content,
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-11",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(
            "Apply the rename from legacy/api/settings.py to "
            "services/api/config.py."
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


def test_historical_removal_question_is_deferred_to_semantic_gate() -> None:
    entry = _memory(
        "historical-removal",
        "Worker runtime v1 used max_idle_jobs; the setting was removed in runtime v3.",
        metadata={"component": "worker-runtime", "version": "1"},
    )
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request("Why was max_idle_jobs removed from the worker runtime?"),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_obsolete_configuration" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier", "challenger"]


@pytest.mark.parametrize(
    ("case_name", "query"),
    (
        (
            "reverse_direction",
            (
                "Apply the rename from services/api/config.py to "
                "legacy/api/settings.py."
            ),
        ),
        (
            "no_direction",
            (
                "Compare migration options between services/api/config.py and "
                "legacy/api/settings.py."
            ),
        ),
    ),
)
def test_rename_requires_explicit_old_to_new_direction(
    case_name: str,
    query: str,
) -> None:
    entry = _memory(
        f"directional-rename-{case_name}",
        "Migration m-3 moved ownership from legacy/api/settings.py to "
        "services/api/config.py.",
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-3",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(query),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


@pytest.mark.parametrize(
    ("case_name", "query", "content", "forbidden_reason"),
    (
        (
            "read_vs_write",
            "Enable read access for the admin service.",
            (
                "Disable write access for the admin service while preserving "
                "read access."
            ),
            "deterministic_action_polarity_conflict",
        ),
        (
            "metrics_vs_docs",
            "Bind the admin metrics service to localhost.",
            (
                "Expose the admin docs service on all interfaces while keeping "
                "admin metrics local."
            ),
            "deterministic_network_exposure_conflict",
        ),
    ),
)
def test_distinct_predicate_objects_do_not_trigger_deterministic_veto(
    case_name: str,
    query: str,
    content: str,
    forbidden_reason: str,
) -> None:
    entry = _memory(f"distinct-predicate-{case_name}", content)
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request(query),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert forbidden_reason not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier", "challenger"]


def test_current_file_outside_projection_cannot_create_legacy_file_admission(
    tmp_path,
) -> None:
    entry = _memory(
        "hidden-seventeenth-current-file",
        "src/module-16.py stores the private banana ledger.",
    )
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(
            query="Review the selected ownership target.",
            current_files=tuple(
                f"src/module-{index:02d}.py" for index in range(17)
            ),
            min_relevance=0.0,
        )
    )

    candidate = next(
        memory for memory in result.candidates if memory.entry_id == entry.id
    )
    assert candidate.path_recall_priority == 0
    assert "full_path_match" not in candidate.reason_codes
    assert "file_match" not in candidate.reason_codes
    assert entry.id not in result.selected_ids
    assert entry.id not in result.rendered_ids


def test_exact_file_context_cannot_join_on_shared_topic_noun_only() -> None:
    entry = _memory(
        "same-file-shared-retry-topic",
        "services/api/config.py enables exponential retry backoff for failed uploads.",
        metadata={"path": "services/api/config.py"},
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(
            "Format retry comments only.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


@pytest.mark.parametrize(
    "query",
    (
        "Explain the removal of max_idle_jobs from the worker runtime.",
        "Describe the rationale for retiring max_idle_jobs from the worker runtime.",
        "说明 worker runtime 下线 max_idle_jobs 的原因。",
    ),
)
def test_historical_removal_paraphrases_defer_to_semantic_gate(query: str) -> None:
    entry = _memory(
        "historical-removal-paraphrase",
        "Worker runtime v1 used max_idle_jobs; the setting was removed in runtime v3.",
        metadata={"component": "worker-runtime", "version": "1"},
    )
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request(query),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_obsolete_configuration" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier", "challenger"]


def test_obsolete_notice_cannot_hide_active_replacement_guidance() -> None:
    entry = _memory(
        "active-replacement-guidance",
        "Worker runtime v1 max_idle_jobs was removed in v3; use "
        "max_active_jobs instead.",
        metadata={"component": "worker-runtime", "version": "1"},
    )
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request("Configure max_active_jobs for the worker runtime."),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_obsolete_configuration" not in (
        result.signals[0].reason_codes
    )
    assert model.call_kinds == ["verifier", "challenger"]


@pytest.mark.parametrize(
    ("content", "query"),
    (
        (
            "Use max_active_jobs for worker runtime because max_idle_jobs v1 "
            "was removed in v3.",
            "Configure max_active_jobs for the worker runtime.",
        ),
        (
            "请改用 max_active_jobs，因为 max_idle_jobs v1 已移除。",
            "配置 worker runtime 的 max_active_jobs。",
        ),
    ),
)
def test_replacement_before_obsolete_marker_defers_to_semantic_gate(
    content: str,
    query: str,
) -> None:
    entry = _memory(
        "replacement-before-removal",
        content,
        metadata={"component": "worker-runtime", "version": "1"},
    )
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request(query),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_obsolete_configuration" not in (
        result.signals[0].reason_codes
    )
    assert model.call_kinds == ["verifier", "challenger"]


def test_negated_old_setting_is_not_active_replacement_guidance() -> None:
    entry = _memory(
        "negated-obsolete-setting",
        "Do not use max_idle_jobs; max_idle_jobs v1 was removed in v3.",
        metadata={"component": "worker-runtime", "version": "1"},
    )
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request("Configure max_active_jobs for the worker runtime."),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_obsolete_configuration" in (
        result.signals[0].reason_codes
    )
    assert model.call_kinds == []


def test_rename_action_cannot_bind_source_from_a_previous_clause() -> None:
    entry = _memory(
        "cross-clause-rename",
        "Migration m-4 moved ownership from legacy/api/settings.py to "
        "services/api/config.py.",
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-4",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(
            "Inspect legacy/api/settings.py; rename docs to "
            "services/api/config.py."
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


@pytest.mark.parametrize(
    ("case_name", "query", "content"),
    (
        (
            "english_distinct_cache_objects",
            "Enable caching for invoice reads.",
            "Disable caching for payroll writes.",
        ),
        (
            "chinese_distinct_report_actions",
            "允许管理员读取报告。",
            "禁止管理员删除报告。",
        ),
    ),
)
def test_shared_topic_does_not_prove_same_policy_predicate(
    case_name: str,
    query: str,
    content: str,
) -> None:
    entry = _memory(f"shared-topic-{case_name}", content)
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request(query),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert (
        "deterministic_action_polarity_conflict"
        not in result.signals[0].reason_codes
    )
    assert model.call_kinds == ["verifier", "challenger"]


def test_exact_file_context_unknown_action_family_fails_closed() -> None:
    entry = _memory(
        "same-file-unknown-review-action",
        "services/api/config.py enables exponential retry backoff for failed uploads.",
        metadata={"path": "services/api/config.py"},
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(
            "Review retry comments only.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_past_tense_set_query_is_not_current_obsolete_intent() -> None:
    entry = _memory(
        "historical-who-set",
        "Worker runtime v1 used max_idle_jobs; the setting was removed in runtime v3.",
        metadata={"component": "worker-runtime", "version": "1"},
    )
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request("Audit who set max_idle_jobs in the worker runtime."),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_obsolete_configuration" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier", "challenger"]


def test_current_file_hidden_suffix_beyond_256_chars_cannot_admit_memory(
    tmp_path,
) -> None:
    long_path = "src/" + ("a" * 260) + "/hidden.py"
    assert len(long_path) == 274
    entry = _memory(
        "hidden-current-file-suffix",
        f"{long_path} stores the private banana ledger.",
    )
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(
            query="Review the selected ownership target.",
            current_files=(long_path,),
            min_relevance=0.0,
        )
    )

    candidate = next(
        memory for memory in result.candidates if memory.entry_id == entry.id
    )
    assert candidate.path_recall_priority == 0
    assert "full_path_match" not in candidate.reason_codes
    assert "file_match" not in candidate.reason_codes
    assert entry.id not in result.selected_ids
    assert entry.id not in result.rendered_ids


def test_rename_from_to_clause_must_belong_to_the_rename_action() -> None:
    entry = _memory(
        "secondary-action-rename",
        "Migration m-5 moved ownership from legacy/api/settings.py to "
        "services/api/config.py.",
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-5",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(
            "Rename docs and copy from legacy/api/settings.py to "
            "services/api/config.py."
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


def test_shared_context_cannot_hide_distinct_polarity_operations() -> None:
    entry = _memory(
        "long-context-write-policy",
        "Disable write access for the production billing invoice cache.",
    )
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request(
            "Enable read access for the production billing invoice cache."
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert (
        "deterministic_action_polarity_conflict"
        not in result.signals[0].reason_codes
    )
    assert model.call_kinds == ["verifier", "challenger"]


def test_exact_file_context_two_topics_cannot_replace_unknown_action_proof() -> None:
    entry = _memory(
        "same-file-two-topic-unknown-actions",
        "services/api/config.py calculates retry comments checksum for audit output.",
        metadata={"path": "services/api/config.py"},
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(
            "Annotate retry comments only.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_chinese_historical_set_query_is_not_current_obsolete_intent() -> None:
    entry = _memory(
        "historical-chinese-who-set",
        "Worker runtime v1 used max_idle_jobs; the setting was removed in runtime v3.",
        metadata={"component": "worker-runtime", "version": "1"},
    )
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request(
            "审计是谁在 worker runtime 中设置了 max_idle_jobs。"
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_obsolete_configuration" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier", "challenger"]


@pytest.mark.parametrize(
    ("case_name", "current_files"),
    (
        (
            "seventeenth_file",
            tuple(f"notes/file-{index:02d}.txt" for index in range(16))
            + ("tests/hidden.test.ts",),
        ),
        (
            "suffix_beyond_256_chars",
            ("src/" + ("a" * 260) + ".test.ts",),
        ),
    ),
)
def test_hidden_current_file_domain_cannot_create_deterministic_admission(
    case_name: str,
    current_files: tuple[str, ...],
    tmp_path,
) -> None:
    entry = _memory(
        f"hidden-domain-{case_name}",
        "Banana retention applies to the test harness.",
    )
    entry.domains = ["testing"]
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(
            query="Inspect banana ownership target carefully.",
            current_files=current_files,
            min_relevance=0.0,
        )
    )

    candidate = next(
        memory for memory in result.candidates if memory.entry_id == entry.id
    )
    assert result.diagnostics["active_domains"] == []
    assert "domain_with_lexical_evidence" not in candidate.reason_codes
    assert entry.id not in result.selected_ids
    assert entry.id not in result.rendered_ids


@pytest.mark.parametrize(
    "query",
    (
        (
            "Do not apply the rename from legacy/api/settings.py to "
            "services/api/config.py; format comments only."
        ),
        (
            "If we ever rename from legacy/api/settings.py to "
            "services/api/config.py, update the docs first."
        ),
    ),
)
def test_rename_proof_requires_affirmative_current_intent(query: str) -> None:
    entry = _memory(
        "non-affirmative-rename",
        "Migration m-6 moved ownership from legacy/api/settings.py to "
        "services/api/config.py.",
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-6",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(query),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


def test_exact_file_timing_relation_cannot_override_opposite_direction() -> None:
    entry = _memory(
        "opposite-retry-timing-direction",
        "services/api/config.py decreases the upload retry timeout.",
        metadata={"path": "services/api/config.py"},
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(
            "Increase the upload retry timeout.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


@pytest.mark.parametrize("hidden_kind", ("seventeenth", "overlong"))
def test_hidden_explicit_domain_cannot_create_deterministic_admission(
    tmp_path,
    hidden_kind: str,
) -> None:
    hidden_domain = "testing" if hidden_kind == "seventeenth" else "d" * 274
    active_domains = (
        tuple(f"domain-{index:02d}" for index in range(16)) + (hidden_domain,)
        if hidden_kind == "seventeenth"
        else (hidden_domain,)
    )
    entry = _memory("hidden-explicit-domain", "Banana ownership guidance.")
    entry.domains = (hidden_domain,)
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(
            query="Review banana orange pear.",
            active_domains=active_domains,
            min_relevance=0.0,
        )
    )

    candidate = next(
        memory for memory in result.candidates if memory.entry_id == entry.id
    )
    assert hidden_domain not in result.diagnostics["active_domains"]
    assert "domain_with_lexical_evidence" not in candidate.reason_codes
    assert entry.id not in result.selected_ids
    assert entry.id not in result.rendered_ids


@pytest.mark.parametrize(
    "query",
    (
        "审查此前将 legacy/api/settings.py 重命名为 services/api/config.py 的记录。",
        (
            "Rename comments only; legacy/api/settings.py -> "
            "services/api/config.py appears in a changelog."
        ),
    ),
)
def test_rename_proof_cannot_borrow_intent_from_historical_or_other_clause(
    query: str,
) -> None:
    entry = _memory(
        "borrowed-rename-intent",
        "Migration m-7 moved ownership from legacy/api/settings.py to "
        "services/api/config.py.",
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-7",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(query),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


def test_shared_secondary_action_cannot_mask_opposite_primary_direction() -> None:
    entry = _memory(
        "opposite-timing-with-shared-inspection",
        (
            "services/api/config.py decreases the upload retry timeout and "
            "inspect the worker logs."
        ),
        metadata={"path": "services/api/config.py"},
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(
            "Increase the upload retry timeout and inspect the worker logs.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_overlong_metadata_path_cannot_become_a_truncated_rename_identity() -> None:
    actual_destination = "services/" + ("a" * 290) + "/config.py"
    truncated_destination = actual_destination[:256]
    entry = _memory(
        "overlong-rename-identity",
        (
            "Migration m-8 moved ownership from legacy/api/settings.py to "
            f"{actual_destination}."
        ),
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": actual_destination,
            "migration_id": "m-8",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(
            "Apply the rename from legacy/api/settings.py to "
            f"{truncated_destination}."
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


@pytest.mark.parametrize(
    ("query", "memory"),
    (
        (
            "Enable upload retries and enable metrics.",
            (
                "services/api/config.py disables upload retries and enables "
                "metrics."
            ),
        ),
        (
            "Start the upload worker and inspect its logs.",
            (
                "services/api/config.py stops the upload worker and inspects "
                "its logs."
            ),
        ),
    ),
)
def test_shared_secondary_relation_cannot_mask_primary_action_polarity(
    query: str,
    memory: str,
) -> None:
    entry = _memory(
        "opposite-primary-action",
        memory,
        metadata={"path": "services/api/config.py"},
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(query, "services/api/config.py"),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_hidden_query_tail_cannot_reverse_a_deterministic_rename() -> None:
    entry = _memory(
        "query-tail-rename",
        "Migration m-9 moved ownership from legacy/api/settings.py to "
        "services/api/config.py.",
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-9",
        },
    )
    model = _SemanticModel(relevant=True)
    query = (
        "Apply the rename from legacy/api/settings.py to services/api/config.py. "
        + ("x" * 2_050)
        + " Do not apply this rename."
    )

    result = _provider(model).adjudicate(
        request=_request(query),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert result.signals[0].reason_codes == ("hybrid_query_input_budget",)
    assert model.call_kinds == []


def test_hidden_memory_tail_cannot_bypass_a_deterministic_conflict() -> None:
    entry = _memory(
        "memory-tail-rename",
        (
            "Migration m-10 moved ownership from legacy/api/settings.py to "
            "services/api/config.py. "
            + ("x" * 4_050)
            + " Do not keep cache marker."
        ),
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-10",
        },
    )
    model = _SemanticModel(relevant=True)

    result = _provider(model).adjudicate(
        request=_request(
            "Keep cache marker and apply the rename from "
            "legacy/api/settings.py to services/api/config.py."
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert result.signals[0].reason_codes == ("hybrid_memory_input_budget",)
    assert model.call_kinds == []


def test_ambiguous_policy_parse_cannot_be_treated_as_no_rename_conflict() -> None:
    entry = _memory(
        "ambiguous-policy-rename",
        (
            "Do not keep alpha marker. Do not keep gamma marker. "
            "Migration m-11 moved ownership from legacy/api/settings.py to "
            "services/api/config.py."
        ),
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-11",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(
            "Keep alpha marker and apply the rename from "
            "legacy/api/settings.py to services/api/config.py."
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


@pytest.mark.parametrize(
    "content",
    (
        "Banana ownership note.",
        (
            "Migration m-12 moved ownership from legacy/api/settings.py to "
            "services/api/config.py: delete all cache entries."
        ),
        (
            "Migration m-12 moved ownership from legacy/api/settings.py to "
            "services/api/config.py: run the unrelated deploy job."
        ),
        (
            "Migration m-12 moved ownership from legacy/api/settings.py to "
            "services/api/config.py: write an unrelated secret."
        ),
    ),
)
def test_rename_metadata_cannot_authorize_non_migration_content(
    content: str,
) -> None:
    entry = _memory(
        "non-migration-content",
        content,
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-12",
        },
    )
    model = _SemanticModel(relevant=False)

    result = _provider(model).adjudicate(
        request=_request(
            "Apply the rename from legacy/api/settings.py to "
            "services/api/config.py."
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]


def test_cached_complete_query_cannot_authorize_a_hidden_negative_tail() -> None:
    entry = _memory(
        "cached-query-tail-rename",
        "Migration m-13 moved ownership from legacy/api/settings.py to "
        "services/api/config.py.",
        metadata={
            "renamed_from": "legacy/api/settings.py",
            "renamed_to": "services/api/config.py",
            "migration_id": "m-13",
        },
    )
    model = _SemanticModel(relevant=False)
    provider = _provider(model)
    instruction = (
        "Apply the rename from legacy/api/settings.py to services/api/config.py."
    )
    complete_query = ("x" * (2_000 - len(instruction) - 1)) + " " + instruction

    first = provider.adjudicate(
        request=_request(complete_query),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )
    second = provider.adjudicate(
        request=_request(complete_query + " Do not apply this rename."),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert first is not None and second is not None
    assert first.signals[0].accepted is True
    assert first.signals[0].reason_codes == ("deterministic_proven_rename",)
    assert second.signals[0].accepted is False
    assert second.signals[0].reason_codes == ("hybrid_query_input_budget",)
    assert second.diagnostics["cache_hit"] is False
    assert model.call_kinds == []


def test_resolver_fragment_cannot_hide_a_full_evidence_direction_conflict() -> None:
    filler = "x" * 520
    entry = _memory(
        "resolver-hidden-direction",
        (
            "services/api/config.py inspect upload retry logs "
            f"{filler}. Decrease the upload retry timeout."
        ),
        metadata={"path": "services/api/config.py"},
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(
            "Inspect upload retry logs "
            f"{filler}. Increase the upload retry timeout.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_exact_file_resolver_requires_the_cited_content_path_to_match() -> None:
    entry = _memory(
        "metadata-content-path-conflict",
        "services/other/config.py inspects upload retry logs.",
        metadata={"path": "services/api/config.py"},
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(
            "Inspect upload retry logs.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_long_path_prefix_cannot_become_an_exact_file_identity() -> None:
    shared_prefix = "services/" + ("a" * 520)
    query_path = shared_prefix + "/left.py"
    memory_path = shared_prefix + "/right.py"
    entry = _memory(
        "long-prefix-path-conflict",
        f"Inspect upload retry logs in {memory_path}.",
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(f"Inspect upload retry logs in {query_path}."),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_ambiguous_full_order_relation_cannot_gain_exact_file_authority() -> None:
    entry = _memory(
        "ambiguous-order-relation",
        (
            "services/api/config.py runs sync after publish. "
            "Inspect logs after deploy."
        ),
        metadata={"path": "services/api/config.py"},
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(
            "Run sync before publish. Inspect logs after deploy.",
            "services/api/config.py",
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_overlong_query_path_cannot_gain_lexical_rescue_priority(tmp_path) -> None:
    long_path = "services/" + ("a" * 260) + "/config.py"
    entry = _memory("overlong-query-path", f"Ownership note for {long_path}.")
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(
        manager,
        hybrid_provider=_provider(_SemanticModel(relevant=False)),
    ).retrieve(
        MemoryRetrievalRequest(
            query=f"Review `{long_path}`.",
            min_relevance=0.0,
        )
    )

    candidate = next(
        memory for memory in result.candidates if memory.entry_id == entry.id
    )
    assert candidate.path_recall_priority == 0
    assert "full_path_match" not in candidate.reason_codes


def test_truncated_entry_path_cannot_gain_lexical_rescue_priority(tmp_path) -> None:
    requested_path = "services/api/config.py"
    prefix = "x" * (2_000 - len(requested_path) - 1)
    entry = _memory(
        "truncated-entry-path",
        f"{prefix} {requested_path}/hidden.py",
    )
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    # Inspect the lexical projection directly. Hybrid correctly rejects this
    # over-budget memory before model input, which would otherwise hide the
    # path-recall property this regression test is meant to isolate.
    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(
            query=f"Review `{requested_path}`.",
            min_relevance=0.0,
        )
    )

    candidate = next(
        memory for memory in result.candidates if memory.entry_id == entry.id
    )
    assert candidate.path_recall_priority == 0
    assert "full_path_match" not in candidate.reason_codes


@pytest.mark.parametrize(
    "content",
    (
        "services/api/config.py/hidden.py stores the banana ledger.",
        "The config.py.bak archive stores the banana ledger.",
        "services/api/config.py备份 stores the banana ledger.",
        "The config.pyβ archive stores the banana ledger.",
        "The config.py２ archive stores the banana ledger.",
        "The config.py\u034f archive stores the banana ledger.",
        "The config.py\ufe0f archive stores the banana ledger.",
        "The config.py\u20dd archive stores the banana ledger.",
        "The config.py\u0338 archive stores the banana ledger.",
        "The config.py\u200dbackup archive stores the banana ledger.",
        "The config.py#backup archive stores the banana ledger.",
        "The config.py:backup archive stores the banana ledger.",
        "The config.py,backup archive stores the banana ledger.",
    ),
)
def test_legacy_file_gate_requires_a_complete_file_identity(
    tmp_path,
    content: str,
) -> None:
    entry = _memory("file-identity-prefix", content)
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(
            query="Review the selected ownership target.",
            current_files=("services/api/config.py",),
            min_relevance=0.0,
        )
    )

    candidate = next(
        memory for memory in result.candidates if memory.entry_id == entry.id
    )
    assert candidate.score.file_score == 0.0
    assert "file_match" not in candidate.reason_codes
    assert entry.id not in result.selected_ids
    assert entry.id not in result.rendered_ids


def test_legacy_file_gate_does_not_promote_case_only_identity(tmp_path) -> None:
    entry = _memory(
        "case-only-file-identity",
        "Services/API/Config.py stores the banana ledger.",
    )
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(
            query="Review the selected ownership target.",
            current_files=("services/api/config.py",),
            min_relevance=0.0,
        )
    )

    candidate = next(
        memory for memory in result.candidates if memory.entry_id == entry.id
    )
    assert candidate.path_recall_priority > 0
    assert candidate.score.file_score == 0.0
    assert "file_match" not in candidate.reason_codes
    assert entry.id not in result.selected_ids


def test_root_file_current_context_keeps_legacy_file_authority(tmp_path) -> None:
    entry = _memory(
        "root-file-identity",
        "pyproject.toml stores the selected ownership target.",
    )
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(
            query="Review the selected ownership target.",
            current_files=("pyproject.toml",),
            min_relevance=0.0,
        )
    )

    candidate = next(
        memory for memory in result.candidates if memory.entry_id == entry.id
    )
    assert candidate.score.file_score == 1.0
    assert "file_match" in candidate.reason_codes
    assert entry.id in result.selected_ids


@pytest.mark.parametrize(
    ("query_path", "memory_path"),
    (
        ("src/@scope/config.py", "vendor/@scope/config.py"),
        (r"C:\repo\config.py", r"D:\repo\config.py"),
    ),
)
def test_complete_path_prefixes_cannot_collapse_into_exact_file_authority(
    query_path: str,
    memory_path: str,
) -> None:
    entry = _memory(
        "distinct-complete-paths",
        f"{memory_path} starts its retry window at the server receipt timestamp.",
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(f"Fix retry timeout in {query_path}."),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes


@pytest.mark.parametrize(
    "path",
    ("src/@scope/config.py", r"C:\repo\config.py"),
)
def test_same_complete_path_can_reach_exact_file_resolution(path: str) -> None:
    entry = _memory(
        "same-complete-path",
        f"{path} starts its retry window at the server receipt timestamp.",
        metadata={"path": path},
    )
    model = _ExactPathOnlyResolver(query_kind="query.task")

    result = _provider(model, resolver=True).adjudicate(
        request=_request(f"Fix retry timeout in {path}."),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "resolver_proven_relevant" in result.signals[0].reason_codes


def test_quoted_unicode_path_keeps_recall_identity(tmp_path) -> None:
    entry = _memory(
        "unicode-path-recall",
        "服务/配置.py stores the selected ownership target.",
        metadata={"path": "服务/配置.py"},
    )
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(
        manager,
        hybrid_provider=_provider(_SemanticModel(relevant=False)),
    ).retrieve(
        MemoryRetrievalRequest(
            query="检查 `服务/配置.py` 的所有权。",
            min_relevance=0.0,
        )
    )

    candidate = next(
        memory for memory in result.candidates if memory.entry_id == entry.id
    )
    assert candidate.path_recall_priority >= 5
    assert "full_path_match" in candidate.reason_codes


def test_unrelated_visible_metadata_cannot_erase_content_path_recall(tmp_path) -> None:
    path = "services/api/config.py"

    def recall_priority(*, with_metadata: bool) -> int:
        entry = _memory(
            f"path-with-metadata-{with_metadata}",
            f"{path} stores the selected ownership target.",
        )
        if with_metadata:
            entry.tags = [f"tag-{index:02d}-" + ("x" * 240) for index in range(16)]
            entry.domains = [
                f"domain-{index:02d}-" + ("y" * 237) for index in range(16)
            ]
        manager = MemoryManager(
            project_root=tmp_path / str(with_metadata),
            data_root=tmp_path / f"user-memory-{with_metadata}",
        )
        manager.memories[MemoryScope.PROJECT].entries.append(entry)
        manager.memories[MemoryScope.PROJECT]._rebuild_indices()
        result = CanonicalMemoryRetriever(
            manager,
            hybrid_provider=_provider(_SemanticModel(relevant=False)),
        ).retrieve(
            MemoryRetrievalRequest(query=f"Review {path}.", min_relevance=0.0)
        )
        candidate = next(
            memory for memory in result.candidates if memory.entry_id == entry.id
        )
        return candidate.path_recall_priority

    assert recall_priority(with_metadata=True) == recall_priority(with_metadata=False) > 0


def test_entry_tag_outside_model_projection_cannot_authorize_admission(
    tmp_path,
) -> None:
    entry = _memory("hidden-seventeenth-tag", "Unrelated banana ledger note.")
    entry.tags = [f"filler-{index:02d}" for index in range(16)] + [
        "checkout retry policy"
    ]
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(query="checkout retry policy", min_relevance=0.0)
    )

    assert entry.id not in result.selected_ids
    assert entry.id not in result.rendered_ids


def test_entry_domain_outside_model_projection_cannot_authorize_admission(
    tmp_path,
) -> None:
    entry = _memory("hidden-seventeenth-domain", "Checkout banana ledger note.")
    entry.domains = [f"domain-{index:02d}" for index in range(16)] + ["security"]
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(entry)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(
            query="checkout hardening",
            active_domains=("security",),
            min_relevance=0.0,
        )
    )

    assert entry.id not in result.selected_ids
    assert entry.id not in result.rendered_ids


def test_superseding_correction_survives_a_tied_lexical_rescue_cap(
    tmp_path,
) -> None:
    class DenseDistractorEncoder:
        def encode_documents(self, texts):
            return tuple(
                (1.0, 0.0)
                if "dense-priority-marker" in text
                else (0.0, 1.0)
                for text in texts
            )

        def encode_queries(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    path = "services/api/config.py"
    dense = [
        _memory(
            f"dense-{index:02d}",
            f"dense-priority-marker unrelated record {index}.",
        )
        for index in range(20)
    ]
    old = [
        _memory(
            f"old-{index:02d}",
            f"{path} contains the selected ownership target banana variant {index}.",
            metadata={"path": path},
        )
        for index in range(12)
    ]
    for entry in old:
        entry.usage_count = 100_000
        entry.usefulness_score = 1.0
    correction = _memory(
        "zz-correction",
        f"{path} contains the selected ownership target.",
        metadata={"path": path, "supersedes": "old-00"},
    )
    correction.provenance = {"evidence_kind": "user_correction"}
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.extend(
        (*dense, *old, correction)
    )
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()
    provider = HybridRuntimeProvider(
        encoder=DenseDistractorEncoder(),
        model_adapter=_SemanticModel(relevant=False),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        embedding_provider="qwen",
    )

    result = CanonicalMemoryRetriever(
        manager,
        hybrid_provider=provider,
    ).retrieve(
        MemoryRetrievalRequest(
            query="Review the selected ownership target.",
            current_files=(path,),
            min_relevance=0.0,
        )
    )

    assert len(result.candidate_ids) == 32
    assert result.candidate_ids[:20] == tuple(item.id for item in dense)
    assert correction.id in result.candidate_ids[20:]
    assert old[-1].id not in result.candidate_ids


def test_correction_authority_cannot_displace_stronger_rescue_evidence(
    tmp_path,
) -> None:
    class DenseDistractorEncoder:
        def encode_documents(self, texts):
            return tuple(
                (1.0, 0.0)
                if "dense-priority-marker" in text
                else (0.0, 1.0)
                for text in texts
            )

        def encode_queries(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    dense = [
        _memory(
            f"strong-dense-{index:02d}",
            f"dense-priority-marker unrelated record {index}.",
        )
        for index in range(20)
    ]
    weak_corrections = [
        _memory(
            f"weak-correction-{index:02d}",
            f"Unrelated checkout note {index}.",
        )
        for index in range(12)
    ]
    for entry in weak_corrections:
        entry.tags = ["checkout"]
        entry.provenance = {"evidence_kind": "user_correction"}
    exact_phrase = _memory(
        "zz-exact-phrase",
        "The checkout coordinator retry policy is authoritative.",
    )
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.extend(
        (*dense, *weak_corrections, exact_phrase)
    )
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()
    provider = HybridRuntimeProvider(
        encoder=DenseDistractorEncoder(),
        model_adapter=_SemanticModel(relevant=False),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        embedding_provider="qwen",
    )

    result = CanonicalMemoryRetriever(
        manager,
        hybrid_provider=provider,
    ).retrieve(
        MemoryRetrievalRequest(
            query="checkout coordinator retry policy",
            min_relevance=0.0,
        )
    )

    assert len(result.candidate_ids) == 32
    assert exact_phrase.id in result.candidate_ids[20:]
    admitted_weak_corrections = {
        entry.id for entry in weak_corrections
    }.intersection(result.candidate_ids)
    assert len(admitted_weak_corrections) == 11


def test_lexical_lane_backfills_after_incomplete_memory_inputs() -> None:
    class DenseFirstEncoder:
        def encode_documents(self, texts):
            return tuple(
                (1.0, 0.0) if "dense-complete-marker" in text else (0.0, 1.0)
                for text in texts
            )

        def encode_queries(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    dense = [
        _memory(
            f"dense-complete-{index:02d}",
            f"dense-complete-marker unrelated record {index}.",
        )
        for index in range(20)
    ]
    oversized = [
        _memory(
            f"oversized-lexical-{index:02d}",
            "Checkout ownership retry rule " + ("x" * 2_100),
        )
        for index in range(12)
    ]
    valid = _memory(
        "valid-lexical-after-oversized",
        "Checkout ownership retry rule uses the selected coordinator.",
    )
    provider = HybridRuntimeProvider(
        encoder=DenseFirstEncoder(),
        model_adapter=_SemanticModel(relevant=False),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        embedding_provider="qwen",
    )

    result = provider.adjudicate(
        request=_request("Review the checkout ownership retry rule."),
        entries=tuple((*dense, *oversized, valid)),
        lexical_accepted_ids=tuple(
            entry.id for entry in (*oversized, valid)
        ),
    )

    assert result is not None
    assert result.candidate_ids[:20] == tuple(entry.id for entry in dense)
    assert result.candidate_ids[20:] == (valid.id,)
    assert len(result.candidate_ids) <= 32
    rejected = {
        signal.entry_id
        for signal in result.signals
        if signal.reason_codes == ("hybrid_memory_input_budget",)
    }
    assert rejected == {entry.id for entry in oversized}
    assert result.diagnostics["dense_lane_count"] == 20
    assert result.diagnostics["lexical_rescue_count"] == 1
    assert result.diagnostics["memory_input_budget_rejected_count"] == 12
    assert result.diagnostics["candidate_budget_rejected_count"] == 0


def test_dense_lane_backfills_after_incomplete_memory_inputs() -> None:
    class RankedEncoder:
        def encode_documents(self, texts):
            return tuple(
                (1.0, 0.0)
                if "oversized-dense-marker" in text
                else (0.5, 0.0)
                for text in texts
            )

        def encode_queries(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    oversized = [
        _memory(
            f"oversized-dense-{index:02d}",
            "oversized-dense-marker " + ("x" * 2_100),
        )
        for index in range(20)
    ]
    valid = [
        _memory(
            f"valid-dense-{index:02d}",
            f"Checkout ownership retry rule record {index}.",
        )
        for index in range(20)
    ]
    provider = HybridRuntimeProvider(
        encoder=RankedEncoder(),
        model_adapter=_SemanticModel(relevant=False),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        embedding_provider="qwen",
    )

    result = provider.adjudicate(
        request=_request("Review the checkout ownership retry rule."),
        entries=tuple((*oversized, *valid)),
        lexical_accepted_ids=(),
    )

    assert result is not None
    assert result.candidate_ids == tuple(entry.id for entry in valid)
    assert len(result.candidate_ids) <= 32
    rejected = {
        signal.entry_id
        for signal in result.signals
        if signal.reason_codes == ("hybrid_memory_input_budget",)
    }
    assert rejected == {entry.id for entry in oversized}
    assert result.diagnostics["dense_lane_count"] == 20
    assert result.diagnostics["lexical_rescue_count"] == 0
    assert result.diagnostics["memory_input_budget_rejected_count"] == 20
